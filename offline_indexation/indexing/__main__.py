"""CLI: index chunked documents into PostgreSQL + pgvector.

    uv run python -m indexing
    uv run python -m indexing --batch-size 32
    uv run python -m indexing --sites autosalubritate.md help.chisinau.md   # a subset, minutes instead of the whole corpus
    uv run python -m indexing --from-jsonl                                  # reuse data/chunks/*.jsonl, no re-chunking

Embeddings: BAAI/bge-m3 (dense 1024) via sentence-transformers (MPS on Mac / CUDA / CPU).
Incremental: skips embedding if content_hash already exists with an embedding.
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from retrieval.db import get_connection, init_db

from chunking.chunker import chunk_document

from .indexer import Indexer

log = logging.getLogger("indexing")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m indexing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data"), help="path to data directory")
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


def chunk_all(parsed_dir: Path, chunks_dir: Path) -> dict[str, list[dict]]:
    """Runs chunking over parsed files and pages, writing jsonl and returning by_doc."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = parsed_dir / "pages"

    paths = []
    if parsed_dir.exists():
        paths.extend(sorted(p for p in parsed_dir.glob("*.json") if not p.name.endswith(".docling.json")))
    if pages_dir.exists():
        paths.extend(sorted(pages_dir.glob("*.json")))

    by_doc: dict[str, list[dict]] = {}
    active_jsonl: set[str] = set()

    for p in paths:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
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
            log.warning("Chunking error on %s: %s", p.name, e)

    # Delete stale .jsonl of documents that no longer exist
    for existing in chunks_dir.glob("*.jsonl"):
        if existing.name not in active_jsonl:
            existing.unlink()

    return by_doc


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    parsed_dir = args.data / "parsed"
    pages_dir = parsed_dir / "pages"
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
    total_parsed_files = len(list(p for p in parsed_dir.glob("*.json") if not p.name.endswith(".docling.json"))) if parsed_dir.exists() else 0
    total_parsed_pages = len(list(pages_dir.glob("*.json"))) if pages_dir.exists() else 0
    total_expected_docs = total_parsed_files + total_parsed_pages

    if not args.from_jsonl:
        log.info("Re-chunking documents from %s...", parsed_dir)
        by_doc = chunk_all(parsed_dir, chunks_dir)
    else:
        log.info("Loading existing chunks from %s (--from-jsonl)...", chunks_dir)
        by_doc = load_chunks_from_disk(chunks_dir)

    if not by_doc:
        print("No chunks found to index.")
        return

    if args.sites:
        by_doc = {d: cs for d, cs in by_doc.items() if cs and cs[0].get("site") in set(args.sites)}
    if args.limit:
        by_doc = dict(list(by_doc.items())[:args.limit])
    if not by_doc:
        print("No chunks match the filters.")
        return

    indexer = Indexer(conn=conn, batch_size=args.batch_size)
    started = time.monotonic()
    all_chunks = [c for chunks in by_doc.values() for c in chunks]
    log.info("Indexing %d chunks from %d documents", len(all_chunks), len(by_doc))

    indexer.upsert_documents(by_doc)  # before chunks: chunks.doc_id references documents
    reused, computed = indexer.embed_and_store(all_chunks)
    stale = indexer.delete_stale_chunks(by_doc)

    # Orphans are only knowable when the whole corpus was chunked in this run.
    is_full_corpus = not (args.from_jsonl or args.sites or args.limit) and len(by_doc) >= total_expected_docs
    orphans_removed = 0
    if args.clean_orphans and is_full_corpus:
        orphans_removed = indexer.clean_orphaned_documents(set(by_doc))
    elif args.clean_orphans:
        log.info("Skipping orphan cleanup: partial run (%d of %d documents)", len(by_doc), total_expected_docs)

    print(f"\nIndexing finished in {time.monotonic() - started:.1f}s:")
    print(f"  Documents indexed:   {len(by_doc)}")
    print(f"  Chunks:              {len(all_chunks)}")
    print(f"  Embeddings reused:   {reused}")
    print(f"  Embeddings computed: {computed}")
    print(f"  Stale chunks removed:{stale:>4}")
    print(f"  Orphans removed:     {orphans_removed}")

    conn.close()



if __name__ == "__main__":
    main()
