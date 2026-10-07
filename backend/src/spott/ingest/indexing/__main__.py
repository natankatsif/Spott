"""CLI: index chunked documents into PostgreSQL + pgvector.

    uv run python -m spott.ingest.indexing
    uv run python -m spott.ingest.indexing --batch-size 32
    uv run python -m spott.ingest.indexing --sites autosalubritate.md help.chisinau.md   # a subset, minutes instead of the whole corpus

The current documents (registry + data/parsed) are chunked in memory on every run, a few seconds for the corpus.
Embeddings: BAAI/bge-m3 (dense 1024) via sentence-transformers (MPS on Mac / CUDA / CPU).
Incremental: skips embedding if content_hash already exists with an embedding.
"""

import argparse
import logging
import time
from pathlib import Path

from spott.core.db import get_connection, init_db
from spott.core.paths import DATA_DIR
from spott.ingest.chunking.chunker import chunk_document, extract_chunk_lines
from spott.ingest.common.loader import load_active_documents

from .indexer import Indexer

log = logging.getLogger("indexing")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.indexing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="path to data directory")
    p.add_argument("--batch-size", type=int, default=32, help="embedding batch size")
    p.add_argument("--recreate", action="store_true", help="drop and recreate database tables (loses all embeddings)")
    p.add_argument("--sites", nargs="+", metavar="ID", help="index only documents from these sites")
    p.add_argument("--limit", type=int, help="index at most N documents")
    p.add_argument("--clean-orphans", action=argparse.BooleanOptionalAction, default=True, help="remove stale documents from index (default: True)")
    return p.parse_args(argv)


def doc_site(doc: dict) -> str | None:
    """The site a parsed document belongs to: a page's own, a file's first source (as the chunker takes it)."""
    return doc.get("site") or ((doc.get("sources") or [{}])[0] or {}).get("site")


def chunk_documents(docs: list[dict]) -> dict[str, list[dict]]:
    """The chunks of each parsed document, by doc_id; a document that fails to chunk is logged and left out."""
    by_doc: dict[str, list[dict]] = {}
    for doc in docs:
        try:
            chunks = chunk_document(doc)
        except Exception as e:
            log.warning("Chunking error on %s: %s", doc.get("doc_id", "(unknown)"), e)
            continue
        if chunks:
            by_doc[chunks[0]["doc_id"]] = chunks
    return by_doc


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 1. Initialize DB schema
    conn = get_connection(autocommit=True)
    if args.recreate:
        log.info("Recreating database schema (--recreate)...")
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS chunks CASCADE;")
            cur.execute("DROP TABLE IF EXISTS documents CASCADE;")
    init_db(conn)

    # 2. The current documents, chunked (only these sites' when given)
    docs = load_active_documents(args.data)
    total_expected_docs = len(docs)
    if args.sites:
        docs = [d for d in docs if doc_site(d) in set(args.sites)]
    log.info("Chunking %d current documents...", len(docs))
    by_doc = chunk_documents(docs)

    if args.sites:
        by_doc = {d: cs for d, cs in by_doc.items() if cs and cs[0].get("site") in set(args.sites)}
    if args.limit:
        by_doc = dict(list(by_doc.items())[:args.limit])
    # A run over whole sites knows every current document of them: the index keeps no others of these sites
    # (removed or replaced files, pages that became 404 or empty). A --limit run knows only a part.
    site_scoped = bool(args.sites) and not args.limit
    if not by_doc:
        if args.clean_orphans and site_scoped:
            removed = Indexer(conn=conn).clean_orphaned_documents(set(), sites=args.sites)
            print(f"No current documents on {', '.join(args.sites)}; removed from the index: {removed}")
        else:
            print("No chunks match the filters." if (args.sites or args.limit) else "No chunks found to index.")
        return

    indexer = Indexer(conn=conn, batch_size=args.batch_size)
    if indexer.progress.cancelled():  # the admin cancelled the job before indexing: leave the index as it is
        print("Cancelled before indexing.")
        return
    started = time.monotonic()
    all_chunks = [c for chunks in by_doc.values() for c in chunks]
    all_lines = []
    for c in all_chunks:
        c_lines = c.get("lines")
        if not c_lines:
            c_lines = extract_chunk_lines(c)
            c["lines"] = c_lines
        all_lines.extend(c_lines)
    indexer.progress.set_total(len(all_chunks) + len(all_lines))
    log.info("Indexing %d chunks from %d documents", len(all_chunks), len(by_doc))

    indexer.upsert_documents(by_doc)  # before chunks: chunks.doc_id references documents
    reused, computed = indexer.embed_and_store(all_chunks)

    log.info("Indexing %d lines from %d chunks", len(all_lines), len(all_chunks))
    reused_lines, computed_lines = indexer.embed_and_store_lines(all_lines)

    stale = indexer.delete_stale_chunks(by_doc)
    by_chunk = {c["chunk_id"]: c.get("lines", []) for c in all_chunks}
    stale_lines = indexer.delete_stale_lines(by_chunk)

    # Orphans are only knowable when the whole corpus was chunked in this run.
    is_full_corpus = not (args.sites or args.limit)
    orphans_removed = 0
    if args.clean_orphans and is_full_corpus:
        orphans_removed = indexer.clean_orphaned_documents(set(by_doc))
    elif args.clean_orphans and site_scoped:
        orphans_removed = indexer.clean_orphaned_documents(set(by_doc), sites=args.sites)
    elif args.clean_orphans:
        log.info("Skipping orphan cleanup: partial run (%d of %d documents)", len(by_doc), total_expected_docs)


    print(f"\nIndexing finished in {time.monotonic() - started:.1f}s:")
    print(f"  Documents indexed:   {len(by_doc)}")
    print(f"  Chunks:              {len(all_chunks)}")
    print(f"  Chunk emb reused:    {reused}")
    print(f"  Chunk emb computed:  {computed}")
    print(f"  Lines indexed:       {len(all_lines)}")
    print(f"  Line emb reused:     {reused_lines}")
    print(f"  Line emb computed:   {computed_lines}")
    print(f"  Stale chunks removed:{stale:>4}")
    print(f"  Stale lines removed: {stale_lines:>4}")
    print(f"  Orphans removed:     {orphans_removed}")
    indexer.progress.finish()

    conn.close()




if __name__ == "__main__":
    main()
