"""CLI: parse downloaded files into the text corpus.

    uv run python -m parsing                    # all files not parsed yet
    uv run python -m parsing --limit 10
    uv run python -m parsing --retry-failed
    uv run python -m parsing --reparse          # parse everything again (full OCR)
    uv run python -m parsing --rebuild          # rebuild JSON/Markdown from cached Docling output, no OCR
    uv run python -m parsing --sha 3ca4 b838    # only these files (sha256 prefixes), whatever their status

Per file, in data/parsed/:
    <sha>.json          corpus representation: metadata, pages, blocks with section paths
    <sha>.md            the same as Markdown, for reading and debugging
    <sha>.docling.json  raw Docling output, lets --rebuild skip OCR
"""

import argparse
import json
import logging
import sqlite3
import time
from collections import Counter
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DoclingDocument

from common.registry import Registry

from .build import (
    PARSER_VERSION,
    SUPPORTED_EXTENSIONS,
    build_document,
    build_markdown,
    make_converter,
    text_layer_pages,
)

log = logging.getLogger("parsing")

OK_STATUSES = {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m parsing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/registry.sqlite"))
    p.add_argument("--data", type=Path, default=Path("data"), help="reads <data>/raw/, writes <data>/parsed/")
    p.add_argument("--limit", type=int, help="at most N files")
    p.add_argument("--sha", nargs="+", metavar="PREFIX", help="only files whose sha256 starts with these")
    p.add_argument("--retry-failed", action="store_true", help="also parse files that failed before")
    p.add_argument("--reparse", action="store_true", help="also parse already parsed files, with OCR")
    p.add_argument("--rebuild", action="store_true",
                   help="re-derive JSON/Markdown of parsed files from cached Docling output")
    p.add_argument("--pages", action="store_true", help="parse crawled HTML pages instead of files")
    p.add_argument("--sites", nargs="+", metavar="ID", help="site filter when parsing pages")
    return p.parse_args()


class FileParser:
    def __init__(self, registry: Registry, data_dir: Path, *, use_cache: bool):
        self.registry = registry
        self.data_dir = data_dir
        self.out_dir = data_dir / "parsed"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self._converter: DocumentConverter | None = None

    @property
    def converter(self) -> DocumentConverter:
        # Created on first use: loading models is slow and --rebuild usually doesn't need them.
        if self._converter is None:
            self._converter = make_converter()
        return self._converter

    def parse(self, row: sqlite3.Row) -> str:
        sha, ext = row["sha256"], row["extension"]
        if ext not in SUPPORTED_EXTENSIONS:
            self.registry.mark_parsed(sha, "unsupported", error=f"extension {ext or '(none)'}")
            return "unsupported"

        path = self.data_dir / row["path"]
        cache = self.out_dir / f"{sha}.docling.json"
        try:
            if self.use_cache and cache.exists():
                doc = DoclingDocument.load_from_json(cache)
            else:
                result = self.converter.convert(path, raises_on_error=False)
                if result.status not in OK_STATUSES:
                    errors = "; ".join(e.error_message for e in result.errors) or "no details"
                    raise RuntimeError(f"docling {result.status.value}: {errors}")
                doc = result.document
                doc.save_as_json(cache, ensure_ascii=False)

            parsed = build_document(
                doc,
                file={k: row[k] for k in ("sha256", "path", "extension", "size", "content_type")},
                sources=self.registry.file_sources(sha),
                text_layer=text_layer_pages(path),
            )
            (self.out_dir / f"{sha}.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
            (self.out_dir / f"{sha}.md").write_text(build_markdown(doc, parsed), encoding="utf-8")
        except Exception as e:
            log.warning("%s failed: %s: %s", sha[:12], type(e).__name__, e)
            self.registry.mark_parsed(sha, "failed", error=f"{type(e).__name__}: {e}")
            return "failed"

        self.registry.mark_parsed(sha, "parsed", parser_version=PARSER_VERSION)
        return "parsed"


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    statuses = ["pending"]
    if args.retry_failed:
        statuses.append("failed")
    if args.reparse or args.rebuild:
        statuses.append("parsed")
    if args.sha:
        statuses = ["pending", "parsed", "failed", "unsupported"]

    registry = Registry(args.db)
    try:
        if args.pages:
            from collections import defaultdict
            from crawler.config import load_sites
            from parsing.html import parse_site_pages

            pages = registry.pages_to_parse(args.sites, args.limit, args.reparse)
            if not pages:
                print("No pages to parse.")
                return
            config_path = args.data / "sources" / "sites.toml"
            categories = {s.id: s.category for s in load_sites(config_path)} if config_path.exists() else {}
            out_dir = args.data / "parsed" / "pages"
            by_site: dict[str, list] = defaultdict(list)
            for row in pages:
                by_site[row["site"]].append(row)

            started = time.monotonic()
            total_stats = {"parsed": 0, "empty": 0, "failed": 0, "boilerplate_dropped": 0}
            for site_id, site_rows in by_site.items():
                t = time.monotonic()
                stats = parse_site_pages(site_rows, args.data, categories, registry, out_dir)
                for k in total_stats:
                    total_stats[k] += stats.get(k, 0)
                log.info("site %s: %d pages parsed, %d empty, %d dropped boilerplate (%.1fs)",
                         site_id, stats["parsed"], stats["empty"], stats["boilerplate_dropped"], time.monotonic() - t)

            print(f"\nPages parsing run ({time.monotonic() - started:.1f}s):")
            print(f"  parsed:              {total_stats['parsed']}")
            print(f"  empty (<200 chars):  {total_stats['empty']}")
            print(f"  failed:              {total_stats['failed']}")
            print(f"  boilerplate dropped: {total_stats['boilerplate_dropped']}")
            counts = registry.status_counts()["pages"]
            print("\nRegistry pages:", ", ".join(f"{k}={v}" for k, v in counts.items()))
            return

        files = registry.files_to_parse(statuses, args.limit, args.sha)
        if not files:
            print("Nothing to parse.")
            return
        parser = FileParser(registry, args.data, use_cache=args.rebuild)
        stats: Counter[str] = Counter()
        started = time.monotonic()
        for i, row in enumerate(files, 1):
            t = time.monotonic()
            outcome = parser.parse(row)
            stats[outcome] += 1
            log.info("[%d/%d] %s %s %.1fs", i, len(files), outcome, row["path"], time.monotonic() - t)

        print(f"\nThis run ({time.monotonic() - started:.0f}s):")
        for outcome in ("parsed", "failed", "unsupported"):
            print(f"  {outcome:12} {stats[outcome]}")
        counts = registry.status_counts()["files"]
        print("\nRegistry files:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
