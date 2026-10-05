"""CLI: index chunked documents into PostgreSQL + pgvector.

    uv run python -m spott.ingest.indexing
    uv run python -m spott.ingest.indexing --batch-size 32
    uv run python -m spott.ingest.indexing --sites autosalubritate.md help.chisinau.md   # a subset, minutes instead of the whole corpus
    uv run python -m spott.ingest.indexing --from-jsonl                                  # reuse data/chunks/*.jsonl, no re-chunking

Embeddings: BAAI/bge-m3 (dense 1024) via sentence-transformers (MPS on Mac / CUDA / CPU).
Incremental: skips embedding if content_hash already exists with an embedding.
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from spott.core.db import get_connection, init_db
from spott.core.paths import DATA_DIR
from spott.ingest.chunking.chunker import chunk_document, extract_chunk_lines

from .indexer import Indexer

log = logging.getLogger("indexing")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.indexing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="path to data directory")
    p.add_argument("--batch-size", type=int, default=32, help="embedding batch size")
    p.add_argument("--from-jsonl", action="store_true", help="load chunks from existing .jsonl files instead of re-chunking from data/parsed")
    p.add_argument("--recreate", action="store_true", help="drop and recreate database tables (loses all embeddings)")
    p.add_argument("--sites", nargs="+", metavar="ID", help="index only documents from these sites")
    p.add_argument("--limit", type=int, help="index at most N documents")
    p.add_argument("--clean-orphans", action=argparse.BooleanOptionalAction, default=True, help="remove stale documents from index (default: True)")
    return p.parse_args(args)


def load_chunks_from_disk(chunks_dir: Path) -> dict[str, list[dict]]:
    """Loads debug chunk jsonl files grouped by doc_id."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for p in chunks_dir.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                by_doc[c["doc_id"]].append(c)
    return by_doc


def doc_site(doc: dict) -> str | None:
    """The site a parsed document belongs to: a page's own, a file's first source (as the chunker takes it)."""
    return doc.get("site") or ((doc.get("sources") or [{}])[0] or {}).get("site")


def chunk_all(parsed_dir: Path, chunks_dir: Path, sites: list[str] | None = None) -> dict[str, list[dict]]:
    """Runs chunking over parsed files and pages (only these sites' when given), writing jsonl and returning
    by_doc. Stale jsonl files are deleted only after a run over every site."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    from spott.ingest.common.loader import load_active_documents

    data_dir = chunks_dir.parent
    docs = load_active_documents(data_dir)
    if sites:
        docs = [d for d in docs if doc_site(d) in set(sites)]

    by_doc: dict[str, list[dict]] = {}
    active_jsonl: set[str] = set()

    for doc in docs:
        try:
            chunks = chunk_document(doc)
            if chunks:
                doc_id = chunks[0]["doc_id"]
                by_doc[doc_id] = chunks
                safe_name = doc_id.replace(":", "_").replace("/", "_").replace("?", "_").replace("&", "_")
                filename = f"{safe_name}.jsonl"
                active_jsonl.add(filename)
                out_file = chunks_dir / filename
                with out_file.open("w", encoding="utf-8") as f:
                    for c in chunks:
                        f.write(json.dumps(c, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("Chunking error on %s: %s", doc.get("doc_id", "(unknown)"), e)

    # Delete stale .jsonl of documents that no longer exist (other sites' files are unknown to a partial run)
    for existing in ([] if sites else chunks_dir.glob("*.jsonl")):
        if existing.name not in active_jsonl:
            existing.unlink()

    return by_doc



def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    parsed_dir = args.data / "parsed"
    chunks_dir = args.data / "chunks"


    # 1. Initialize DB schema
    conn = get_connection(autocommit=True)
    if args.recreate:
        log.info("Recreating database schema (--recreate)...")
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS chunks CASCADE;")
            cur.execute("DROP TABLE IF EXISTS documents CASCADE;")
    init_db(conn)

    # 2. Get chunks to index (default: rechunk from data/parsed)
    from spott.ingest.common.loader import load_active_documents
    active_docs = load_active_documents(args.data)
    total_expected_docs = len(active_docs)

    if not args.from_jsonl:
        log.info("Re-chunking active documents from registry/parsed...")
        by_doc = chunk_all(parsed_dir, chunks_dir, args.sites)
    else:
        log.info("Loading existing chunks from %s (--from-jsonl)...", chunks_dir)
        by_doc = load_chunks_from_disk(chunks_dir)

    if args.sites:
        by_doc = {d: cs for d, cs in by_doc.items() if cs and cs[0].get("site") in set(args.sites)}
    if args.limit:
        by_doc = dict(list(by_doc.items())[:args.limit])
    # A run over whole sites knows every current document of them: the index keeps no others of these sites
    # (removed or replaced files, pages that became 404 or empty). --limit / --from-jsonl runs know only a part.
    site_scoped = bool(args.sites) and not (args.from_jsonl or args.limit)
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
    is_full_corpus = not (args.from_jsonl or args.sites or args.limit)
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
