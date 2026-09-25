"""CLI: chunk parsed files and pages into search chunks.

    uv run python -m chunking
    uv run python -m chunking --limit 50
    uv run python -m chunking --files-only
    uv run python -m chunking --pages-only

Saves debug chunks in data/chunks/<doc_id_safe>.jsonl
"""

import argparse
import json
import logging
import statistics
import time
from collections import Counter
from pathlib import Path

from .chunker import chunk_document

log = logging.getLogger("chunking")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m chunking", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data"), help="reads data/parsed/, writes data/chunks/")
    p.add_argument("--limit", type=int, help="limit number of documents processed")
    p.add_argument("--files-only", action="store_true", help="only chunk parsed files")
    p.add_argument("--pages-only", action="store_true", help="only chunk parsed pages")
    return p.parse_args()


def safe_id(doc_id: str) -> str:
    return doc_id.replace(":", "_").replace("/", "_").replace("?", "_").replace("&", "_")


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    parsed_dir = args.data / "parsed"
    pages_dir = parsed_dir / "pages"
    chunks_dir = args.data / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    file_paths: list[Path] = []
    if not args.pages_only and parsed_dir.exists():
        file_paths.extend(sorted(p for p in parsed_dir.glob("*.json") if not p.name.endswith(".docling.json")))

    page_paths: list[Path] = []
    if not args.files_only and pages_dir.exists():
        page_paths.extend(sorted(pages_dir.glob("*.json")))

    doc_paths = file_paths + page_paths
    if args.limit:
        doc_paths = doc_paths[:args.limit]

    if not doc_paths:
        print("No parsed documents found in data/parsed/.")
        return

    started = time.monotonic()
    all_chunks: list[dict] = []
    docs_processed = 0

    for path in doc_paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            chunks = chunk_document(doc)
            if chunks:
                doc_id = chunks[0]["doc_id"]
                out_file = chunks_dir / f"{safe_id(doc_id)}.jsonl"
                with out_file.open("w", encoding="utf-8") as f:
                    for c in chunks:
                        f.write(json.dumps(c, ensure_ascii=False) + "\n")
                all_chunks.extend(chunks)
                docs_processed += 1
        except Exception as e:
            log.warning("Failed chunking %s: %s", path.name, e)

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
