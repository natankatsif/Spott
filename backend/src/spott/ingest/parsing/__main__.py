"""CLI: parse downloaded files into the text corpus.

    uv run python -m spott.ingest.parsing                    # all files not parsed yet
    uv run python -m spott.ingest.parsing --limit 10
    uv run python -m spott.ingest.parsing --retry-failed
    uv run python -m spott.ingest.parsing --reparse          # parse everything again (full OCR)
    uv run python -m spott.ingest.parsing --rebuild          # rebuild JSON/Markdown from cached Docling output, no OCR
    uv run python -m spott.ingest.parsing --sha 3ca4 b838    # only these files (sha256 prefixes), whatever their status

Per file, in data/parsed/:
    <sha>.json          corpus representation: metadata, pages, blocks with section paths
    <sha>.md            the same as Markdown, for reading and debugging
    <sha>.docling.json  raw Docling output, lets --rebuild skip OCR
"""

import argparse
import gc
import json
import logging
import time
from collections import Counter
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DoclingDocument

from spott.core.paths import DATA_DIR
from spott.ingest.common.progress import Progress
from spott.ingest.common.registry import Registry

from .build import (
    SUPPORTED_EXTENSIONS,
    build_document,
    build_markdown,
    make_converter,
    needs_ocr,
    page_chars,
    text_layer_pages,
)

log = logging.getLogger("parsing")

OK_STATUSES = {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.parsing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="reads <data>/raw/, writes <data>/parsed/")
    p.add_argument("--limit", type=int, help="at most N files")
    p.add_argument("--sha", nargs="+", metavar="PREFIX", help="only files whose sha256 starts with these")
    p.add_argument("--retry-failed", action="store_true", help="also parse files that failed before")
    p.add_argument("--reparse", action="store_true", help="also parse already parsed files, with OCR")
    p.add_argument("--rebuild", action="store_true",
                   help="re-derive JSON/Markdown of parsed files from cached Docling output")
    p.add_argument("--pages", action="store_true", help="parse crawled HTML pages instead of files")
    p.add_argument("--sites", nargs="+", metavar="ID", help="only these sites (their files, or their pages with --pages)")
    p.add_argument("--keys-file", type=Path,
                   help="only the pending files of these documents (the \"documents\" of a check's JSON)")
    return p.parse_args(argv)


class FileParser:
    def __init__(self, registry: Registry, data_dir: Path, *, use_cache: bool):
        self.registry = registry
        self.data_dir = data_dir
        self.out_dir = data_dir / "parsed"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self._converters: dict[bool, DocumentConverter] = {}

    def converter(self, ocr: bool) -> DocumentConverter:
        # Created on first use (loading models is slow, --rebuild usually needs none), and only one at a time: each
        # holds its own layout and table models, and two of them next to a large PDF do not fit an 8 GB server.
        # main() puts a batch's text-layer files first, so a run switches at most once.
        if ocr not in self._converters:
            self._converters.clear()
            gc.collect()
            self._converters[ocr] = make_converter(ocr=ocr)
        return self._converters[ocr]

    def parse(self, row: dict) -> tuple[str, bool]:
        """(outcome, whether this file went through OCR)."""
        sha, ext = row["sha256"], row["extension"]
        if ext not in SUPPORTED_EXTENSIONS:
            self.registry.mark_parsed(sha, "unsupported", error=f"extension {ext or '(none)'}")
            return "unsupported", False

        path = self.data_dir / row["path"]
        cache = self.out_dir / f"{sha}.docling.json"
        chars = page_chars(path)  # decides OCR below, and which pages count as scans in the corpus JSON
        ocr = needs_ocr(chars)
        # If the kernel kills the run on this file, it stays 'parsing' and the next run fails it instead of
        # picking it first again (Registry.fail_interrupted_parses).
        self.registry.mark_parsed(sha, "parsing")
        try:
            if self.use_cache and cache.exists():
                doc = DoclingDocument.load_from_json(cache)
            else:
                result = self.converter(ocr).convert(path, raises_on_error=False)
                if result.status not in OK_STATUSES:
                    errors = "; ".join(e.error_message for e in result.errors) or "no details"
                    raise RuntimeError(f"docling {result.status.value}: {errors}")
                doc = result.document
                doc.save_as_json(cache, ensure_ascii=False)

            parsed = build_document(
                doc,
                file={k: row[k] for k in ("sha256", "path", "extension")},
                sources=self.registry.file_sources(sha),
                text_layer=text_layer_pages(chars),
            )
            (self.out_dir / f"{sha}.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
            (self.out_dir / f"{sha}.md").write_text(build_markdown(doc, parsed), encoding="utf-8")
        except Exception as e:
            log.warning("%s failed: %s: %s", sha[:12], type(e).__name__, e)
            self.registry.mark_parsed(sha, "failed", error=f"{type(e).__name__}: {e}")
            return "failed", ocr

        self.registry.mark_parsed(sha, "parsed")
        return "parsed", ocr


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

    registry = Registry.open()
    try:
        if killed := registry.fail_interrupted_parses():
            log.warning("%d file(s) were being parsed when a previous run was killed: marked failed", killed)
        if args.pages:
            from spott.ingest.pages_parsing.__main__ import run_pages_parsing

            run_pages_parsing(
                registry=registry,
                data_dir=args.data,
                sites=args.sites,
                limit=args.limit,
                reparse=args.reparse,
            )
            return

        if args.keys_file:  # a check: its documents only, not every pending file of every site
            keys = json.loads(args.keys_file.read_text(encoding="utf-8")).get("documents") or []
            files = registry.files_of_documents(keys, statuses)
        else:
            files = registry.files_to_parse(statuses, args.limit, args.sha, args.sites)
        if not files:
            print("Nothing to parse.")
            return
        # text-layer files first, scans last: one converter at a time, so this is at most one model switch
        files = sorted(files, key=lambda row: needs_ocr(page_chars(args.data / row["path"])))
        parser = FileParser(registry, args.data, use_cache=args.rebuild)
        stats: Counter[str] = Counter()
        started = time.monotonic()
        progress = Progress(total=len(files))
        for i, row in enumerate(files, 1):
            if progress.cancelled():
                break
            t = time.monotonic()
            outcome, ocr = parser.parse(row)
            stats[outcome] += 1
            stats["ocr" if ocr else "text layer"] += outcome == "parsed"
            progress.advance(error=outcome == "failed")
            log.info("[%d/%d] %s%s %s %.1fs", i, len(files), outcome, " (ocr)" if ocr else "", row["path"],
                     time.monotonic() - t)
        progress.finish()

        print(f"\nThis run ({time.monotonic() - started:.0f}s):")
        for outcome in ("parsed", "failed", "unsupported"):
            print(f"  {outcome:12} {stats[outcome]}")
        print(f"  of them: {stats['text layer']} from the text layer, {stats['ocr']} through OCR")
        counts = registry.status_counts()["files"]
        print("\nRegistry files:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
