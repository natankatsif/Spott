"""CLI: parse crawled HTML pages into the text corpus.

    uv run python -m pages_parsing
    uv run python -m pages_parsing --sites dgaurf.md help.chisinau.md
    uv run python -m pages_parsing --limit 100
    uv run python -m pages_parsing --reparse

Per page, in data/parsed/pages/:
    <url_hash>.json    page corpus representation: metadata, blocks with section paths
"""

import argparse
import logging
import time
from collections import defaultdict
from pathlib import Path

from common.registry import Registry
from crawler.config import load_sites
from parsing.html import parse_site_pages

log = logging.getLogger("pages_parsing")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pages_parsing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/registry.sqlite"))
    p.add_argument("--data", type=Path, default=Path("data"), help="reads <data>/crawl/, writes <data>/parsed/pages/")
    p.add_argument("--config", type=Path, default=Path("data/sources/sites.toml"))
    p.add_argument("--sites", nargs="+", metavar="ID", help="only these site ids")
    p.add_argument("--limit", type=int, help="at most N pages")
    p.add_argument("--reparse", action="store_true", help="reparse even if already parsed")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    registry = Registry(args.db)
    try:
        pages = registry.pages_to_parse(args.sites, args.limit, args.reparse)
        if not pages:
            print("No pages to parse.")
            return

        categories = {s.id: s.category for s in load_sites(args.config)} if args.config.exists() else {}
        out_dir = args.data / "parsed" / "pages"

        # Group by site for site-wide boilerplate filtering
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
    finally:
        registry.close()


if __name__ == "__main__":
    main()
