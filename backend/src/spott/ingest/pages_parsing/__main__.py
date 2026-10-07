"""CLI: parse crawled HTML pages into the text corpus.

    uv run python -m spott.ingest.pages_parsing
    uv run python -m spott.ingest.pages_parsing --sites dgaurf.md help.chisinau.md
    uv run python -m spott.ingest.pages_parsing --limit 100
    uv run python -m spott.ingest.pages_parsing --reparse

Per page, in data/parsed/pages/:
    <url_hash>.json    page corpus representation: metadata, blocks with section paths
"""

import argparse
import logging
import time
from collections import defaultdict
from pathlib import Path

from spott.core.paths import DATA_DIR, SITES_TOML
from spott.ingest.common.progress import Progress
from spott.ingest.common.registry import Registry
from spott.ingest.crawler.config import load_sites, load_sites_from_db
from spott.ingest.parsing.html import parse_site_pages

log = logging.getLogger("pages_parsing")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.pages_parsing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="reads <data>/crawl/, writes <data>/parsed/pages/")
    p.add_argument("--config", type=Path, default=SITES_TOML)
    p.add_argument("--sites", nargs="+", metavar="ID", help="only these site ids")
    p.add_argument("--limit", type=int, help="at most N pages")
    p.add_argument("--reparse", action="store_true", help="reparse even if already parsed")
    return p.parse_args(argv)


def run_pages_parsing(
    registry: Registry,
    data_dir: Path,
    config_path: Path,
    sites: list[str] | None = None,
    limit: int | None = None,
    reparse: bool = False,
) -> dict:
    pages = registry.pages_to_parse(sites, limit, reparse)
    if not pages:
        print("No pages to parse.")
        return {}

    sites_list = load_sites_from_db() or (load_sites(config_path) if config_path.exists() else [])
    categories = {s.id: s.category for s in sites_list}
    out_dir = data_dir / "parsed" / "pages"

    # Group by site for site-wide boilerplate filtering
    by_site: dict[str, list] = defaultdict(list)
    for row in pages:
        by_site[row["site"]].append(row)

    started = time.monotonic()
    total_stats = {"parsed": 0, "empty": 0, "failed": 0, "boilerplate_dropped": 0}
    progress = Progress(total=len(pages))

    for site_id, site_rows in by_site.items():
        if progress.cancelled():
            break
        t = time.monotonic()
        stats = parse_site_pages(site_rows, data_dir, categories, registry, out_dir,
                                 context=registry.site_pages(site_id))
        progress.advance(len(site_rows))
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
        log.info(
            "site %s: %d pages parsed, %d empty, %d dropped boilerplate (%.1fs)",
            site_id,
            stats["parsed"],
            stats["empty"],
            stats["boilerplate_dropped"],
            time.monotonic() - t,
        )

    progress.finish()
    print(f"\nPages parsing run ({time.monotonic() - started:.1f}s):")
    print(f"  parsed:              {total_stats['parsed']}")
    print(f"  empty (<200 chars):  {total_stats['empty']}")
    print(f"  failed:              {total_stats['failed']}")
    print(f"  boilerplate dropped: {total_stats['boilerplate_dropped']}")

    counts = registry.status_counts()["pages"]
    print("\nRegistry pages:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return total_stats


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    registry = Registry.open()
    try:
        run_pages_parsing(
            registry=registry,
            data_dir=args.data,
            config_path=args.config,
            sites=args.sites,
            limit=args.limit,
            reparse=args.reparse,
        )
    finally:
        registry.close()


if __name__ == "__main__":
    main()
