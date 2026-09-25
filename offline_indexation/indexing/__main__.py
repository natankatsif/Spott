"""CLI: index chunked documents into PostgreSQL + pgvector.

    uv run python -m indexing
    uv run python -m indexing --batch-size 32
    uv run python -m indexing --rechunk

Embeddings: BAAI/bge-m3 (dense 1024) via sentence-transformers (MPS on Mac / CUDA / CPU).
Incremental: skips embedding if content_hash already exists with an embedding.
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from chunking.chunker import chunk_document

from .db import get_connection, init_db
from .indexer import Indexer

log = logging.getLogger("indexing")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m indexing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data"), help="path to data directory")
    p.add_argument("--batch-size", type=int, default=32, help="embedding batch size")
    p.add_argument("--from-jsonl", action="store_true", help="load chunks from existing .jsonl files instead of re-chunking from data/parsed")
    p.add_argument("--recreate", action="store_true", help="drop and recreate database tables")
    p.add_argument("--clean-orphans", action=argparse.BooleanOptionalAction, default=True, help="remove stale documents from index (default: True)")
    return p.parse_args(args)


def load_chunks_from_disk(chunks_dir: Path) -> dict[str, list[dict]]:
    """Loads debug chunk jsonl files grouped by doc_id."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for p in chunks_dir.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
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

    indexer = Indexer(conn=conn, batch_size=args.batch_size)
    started = time.monotonic()
    total_reused = 0
    total_computed = 0
    total_chunks = 0

    all_chunks = [c for chunks in by_doc.values() for c in chunks]
    total_chunks = len(all_chunks)
    log.info("Found %d total chunks across %d documents.", total_chunks, len(by_doc))

    # Compute/fetch embeddings for all chunks in batch
    reused, computed = indexer.compute_embeddings(all_chunks)
    total_reused += reused
    total_computed += computed

    # Upsert each document and its chunks
    for i, (doc_id, chunks) in enumerate(by_doc.items(), 1):
        indexer.index_document(doc_id, chunks)
        if i % 25 == 0 or i == len(by_doc):
            log.info("Indexed %d/%d documents...", i, len(by_doc))

    # Clean orphaned documents only if requested and full corpus was processed
    orphans_removed = 0
    is_full_corpus = (not args.from_jsonl) and (len(by_doc) >= total_expected_docs)
    if args.clean_orphans and is_full_corpus:
        orphans_removed = indexer.clean_orphaned_documents(set(by_doc.keys()))
    elif args.clean_orphans and not is_full_corpus:
        log.info(
            "Skipping orphan cleanup: partial corpus processed (%d docs indexed vs %d expected).",
            len(by_doc),
            total_expected_docs,
        )

    print(f"\nIndexing finished in {time.monotonic() - started:.1f}s:")
    print(f"  Documents indexed:  {len(by_doc)}")
    print(f"  Total chunks:       {total_chunks}")
    print(f"  Embeddings reused:  {total_reused}")
    print(f"  Embeddings computed:{total_computed}")
    if orphans_removed:
        print(f"  Orphans removed:    {orphans_removed}")

    conn.close()



if __name__ == "__main__":
    main()
