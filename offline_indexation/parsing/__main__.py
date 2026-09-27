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

from common.progress import Progress
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
    p.add_argument("--keys-file", type=Path,
                   help="only the pending files of these documents (the \"documents\" of a check's JSON)")
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
            from pages_parsing.__main__ import run_pages_parsing

            config_path = args.data / "sources" / "sites.toml"
            run_pages_parsing(
                registry=registry,
                data_dir=args.data,
                config_path=config_path,
                sites=args.sites,
                limit=args.limit,
                reparse=args.reparse,
            )
            return

        if args.keys_file:  # a check: its documents only, not every pending file of every site
            keys = json.loads(args.keys_file.read_text(encoding="utf-8")).get("documents") or []
            files = registry.files_of_documents(keys, statuses)
        else:
            files = registry.files_to_parse(statuses, args.limit, args.sha)
        if not files:
            print("Nothing to parse.")
            return
        parser = FileParser(registry, args.data, use_cache=args.rebuild)
        stats: Counter[str] = Counter()
        started = time.monotonic()
        progress = Progress(total=len(files))
        for i, row in enumerate(files, 1):
            if progress.cancelled():
                break
            t = time.monotonic()
            outcome = parser.parse(row)
            stats[outcome] += 1
            progress.advance(error=outcome == "failed")
            log.info("[%d/%d] %s %s %.1fs", i, len(files), outcome, row["path"], time.monotonic() - t)
        progress.finish()

        print(f"\nThis run ({time.monotonic() - started:.0f}s):")
        for outcome in ("parsed", "failed", "unsupported"):
            print(f"  {outcome:12} {stats[outcome]}")
        counts = registry.status_counts()["files"]
        print("\nRegistry files:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
