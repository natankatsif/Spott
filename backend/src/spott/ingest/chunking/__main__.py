"""CLI: chunk parsed files and pages into search chunks.

    uv run python -m spott.ingest.chunking
    uv run python -m spott.ingest.chunking --limit 50
    uv run python -m spott.ingest.chunking --files-only
    uv run python -m spott.ingest.chunking --pages-only

Saves debug chunks in data/chunks/<doc_id_safe>.jsonl
"""

import argparse
import json
import logging
import statistics
import time
from collections import Counter
from pathlib import Path

from spott.core.paths import DATA_DIR
from spott.ingest.common.paths import safe_filename

from .chunker import chunk_document

log = logging.getLogger("chunking")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.chunking", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="reads data/parsed/, writes data/chunks/")
    p.add_argument("--limit", type=int, help="limit number of documents processed")
    p.add_argument("--files-only", action="store_true", help="only chunk parsed files")
    p.add_argument("--pages-only", action="store_true", help="only chunk parsed pages")
    p.add_argument("--stats", action="store_true", help="print corpus statistics without re-chunking")
    return p.parse_args(argv)


def safe_id(doc_id: str) -> str:
    return safe_filename(doc_id)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.stats:
        from scripts.corpus_stats import (
            calculate_corpus_metrics,
            generate_markdown_table,
        )
        m = calculate_corpus_metrics(args.data)
        print(f"Documents: {m['parsed_files']} files, {m['parsed_pages']} pages (Total: {m['total_docs']})")
        print(f"Chunks:    {m['total_chunks']} (file: {m['file_chunks']}, page: {m['page_chunks']})")
        print(f"Lengths:   min={m['min_len']}, median={m['med_len']}, max={m['max_len']}")
        print(f"<80 chars: {m['less_80']} ({m['pct_less_80']:.2f}%), file<80={m['file_less_80']}")
        print(f"Legal:     {m['with_legal']} ({m['pct_legal']:.2f}%)")
        print(f"BBoxes:    {m['file_with_bbox']}/{m['total_file_bbox']} ({m['pct_bbox']:.1f}%)")
        print(f"Tables:    {m['tables']} ({m['pct_tables']:.2f}%)")
        print(f"Contacts:  {m['contacts']} ({m['pct_contacts']:.2f}%)")
        print(f"Dupes:     {m['dupe_hashes']}")
        print("\n" + generate_markdown_table(m))
        return

    chunks_dir = args.data / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)


    from spott.ingest.common.loader import load_active_documents

    docs = load_active_documents(
        args.data,
        files_only=args.files_only,
        pages_only=args.pages_only,
        limit=args.limit,
    )

    if not docs:
        print("No active parsed documents found.")
        return

    started = time.monotonic()
    all_chunks: list[dict] = []
    docs_processed = 0

    active_files: set[str] = set()

    for doc in docs:
        try:
            chunks = chunk_document(doc)
            if chunks:
                doc_id = chunks[0]["doc_id"]
                filename = f"{safe_id(doc_id)}.jsonl"
                active_files.add(filename)
                out_file = chunks_dir / filename
                with out_file.open("w", encoding="utf-8") as f:
                    for c in chunks:
                        f.write(json.dumps(c, ensure_ascii=False) + "\n")
                all_chunks.extend(chunks)
                docs_processed += 1
        except Exception as e:
            log.warning("Failed chunking %s: %s", doc.get("doc_id", "(unknown)"), e)


    # Delete stale .jsonl of documents that no longer exist (only on full runs)
    if not args.limit and not args.files_only and not args.pages_only:
        for existing in chunks_dir.glob("*.jsonl"):
            if existing.name not in active_files:
                existing.unlink()

    # Calculate statistics
    total = len(all_chunks)
    by_kind = Counter(c["kind"] for c in all_chunks)
    by_lang = Counter(c["lang"] for c in all_chunks)
    by_cat = Counter(c["category"] or "(none)" for c in all_chunks)

    lengths = [c["char_count"] for c in all_chunks]
    min_len = min(lengths) if lengths else 0
    max_len = max(lengths) if lengths else 0
    med_len = round(statistics.median(lengths)) if lengths else 0

    with_legal = sum(1 for c in all_chunks if c.get("legal_path"))
    with_bboxes = sum(1 for c in all_chunks if c.get("bboxes"))
    tables = sum(1 for c in all_chunks if c.get("is_table"))
    contacts = sum(1 for c in all_chunks if c.get("has_contacts"))

    pct_legal = (with_legal / total * 100) if total else 0.0
    pct_bboxes = (with_bboxes / total * 100) if total else 0.0
    pct_tables = (tables / total * 100) if total else 0.0
    pct_contacts = (contacts / total * 100) if total else 0.0

    print(f"\nChunking finished in {time.monotonic() - started:.1f}s:")
    print(f"  Documents processed: {docs_processed}")
    print(f"  Total chunks:        {total}")
    print(f"  By kind:             {dict(by_kind)}")
    print(f"  By lang:             {dict(by_lang)}")
    print(f"  By category:         {dict(by_cat)}")
    print(f"  Lengths (chars):     min={min_len}, median={med_len}, max={max_len}")
    print(f"  With legal_path:     {with_legal} ({pct_legal:.1f}%)")
    print(f"  With bboxes:         {with_bboxes} ({pct_bboxes:.1f}%)")
    print(f"  Tables:              {tables} ({pct_tables:.1f}%)")
    print(f"  Has contacts:        {contacts} ({pct_contacts:.1f}%)")


if __name__ == "__main__":
    main()
