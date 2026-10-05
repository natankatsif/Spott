"""CLI: breadth-first crawl of the Annex 1 sites.

    uv run python -m spott.ingest.crawler                      # all sites
    uv run python -m spott.ingest.crawler --sites dgaurf.md help.chisinau.md --max-depth 2
    uv run python -m spott.ingest.crawler --resume             # continue an interrupted run
    uv run python -m spott.ingest.crawler --list
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from spott.core.paths import DATA_DIR, REGISTRY, SITES_TOML
from spott.ingest.common.http import make_clients
from spott.ingest.common.registry import Registry

from .config import Site, load_sites, load_sites_from_db
from .site import SiteCrawler

log = logging.getLogger("crawler")
DEFAULT_CONFIG = SITES_TOML


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.crawler", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path,
                   help="a sites TOML instead of the admin panel's sources (default: the database; "
                        "data/sources/sites.toml while it has no sources)")
    p.add_argument("--out", type=Path, default=DATA_DIR / "crawl", help="raw HTML and crawl state")
    p.add_argument("--db", type=Path, default=REGISTRY)
    p.add_argument("--sites", nargs="+", metavar="ID", help="site ids from the config (default: all)")
    p.add_argument("--max-depth", type=int, help="override max_depth for every site")
    p.add_argument("--max-pages", type=int, help="override max_pages for every site")
    p.add_argument("--delay", type=float, help="override delay between requests, seconds")
    p.add_argument("--start-urls", nargs="+", metavar="URL", help="start from these URLs instead of the site's")
    p.add_argument("--path-prefix", help="follow only links under this path (e.g. /ro/servicii)")
    p.add_argument("--start-urls-file", type=Path,
                   help="start from the \"pages\" of this JSON (a check's changes); nothing to do when it lists none")
    p.add_argument("--partial", action="store_true",
                   help="visit the start pages and only links not crawled before; nothing is marked missing or "
                        "dropped (a check, not a complete crawl)")
    p.add_argument("--concurrency", type=int, default=6, help="sites crawled in parallel")
    p.add_argument("--ignore-robots", action="store_true", help="ignore robots.txt on every site")
    p.add_argument("--resume", action="store_true", help="continue from data/crawl/<site>/state.json")
    p.add_argument("--list", action="store_true", help="print configured sites and exit")
    return p.parse_args()


def select_sites(args: argparse.Namespace) -> list[Site]:
    sites = (None if args.config else load_sites_from_db()) or load_sites(args.config or DEFAULT_CONFIG)
    if args.sites:
        by_id = {s.id: s for s in sites}
        if unknown := [i for i in args.sites if i not in by_id]:
            sys.exit(f"Unknown site ids: {', '.join(unknown)}. See --list.")
        sites = [by_id[i] for i in args.sites]
    for site in sites:
        if args.max_depth is not None:
            site.max_depth = args.max_depth
        if args.max_pages is not None:
            site.max_pages = args.max_pages
        if args.delay is not None:
            site.delay = args.delay
        if args.start_urls:
            site.start_urls = args.start_urls
        if args.start_urls_file:
            site.start_urls = json.loads(args.start_urls_file.read_text(encoding="utf-8")).get("pages") or []
        if args.path_prefix:
            site.path_prefix = args.path_prefix
    return sites


async def crawl_all(sites: list[Site], args: argparse.Namespace) -> dict[str, dict]:
    semaphore = asyncio.Semaphore(args.concurrency)
    registry = Registry(args.db)

    async with make_clients() as (client, insecure_client):

        async def crawl_one(site: Site) -> tuple[str, dict]:
            async with semaphore:
                log.info("start %s", site.id)
                crawler = SiteCrawler(site, client, insecure_client, registry, args.out,
                                      respect_robots=not args.ignore_robots, partial=args.partial)
                try:
                    stats = await crawler.run(resume=args.resume)
                    if args.start_urls_file and crawler.new_documents:  # a check: the downloader takes them too
                        changes = json.loads(args.start_urls_file.read_text(encoding="utf-8"))
                        changes["documents"] = sorted({*changes.get("documents", []), *crawler.new_documents})
                        args.start_urls_file.write_text(json.dumps(changes, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
                except Exception as e:
                    log.exception("%s failed", site.id)
                    stats = {"failed": f"{type(e).__name__}: {e}"}
                log.info("done %s %s", site.id, stats)
                return site.id, stats

        try:
            return dict(await asyncio.gather(*(crawl_one(s) for s in sites)))
        finally:
            registry.close()


def write_summary(out: Path, results: dict[str, dict]) -> None:
    path = out / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    summary |= results
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'site':28} {'pages':>6} {'docs':>6} {'new':>5} {'wp':>5} {'errors':>6} {'robots':>6} {'queue':>6}")
    for site_id, s in results.items():
        if "failed" in s:
            print(f"{site_id:28} FAILED: {s['failed']}")
            continue
        print(f"{site_id:28} {s['pages']:>6} {s['documents']:>6} {s['new_documents']:>5} {s['wp_media']:>5} "
              f"{s['errors']:>6} {s['robots_blocked']:>6} {s['queue_left']:>6}")
    print(f"\nSummary: {path}")


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    sites = select_sites(args)
    if args.start_urls_file and not any(s.start_urls for s in sites):
        print("No changed pages to crawl.")
        return
    if args.list:
        for s in sites:
            print(f"{s.id:28} {s.category:16} depth={s.max_depth} pages={s.max_pages}  {' '.join(s.start_urls)}")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    write_summary(args.out, asyncio.run(crawl_all(sites, args)))


if __name__ == "__main__":
    main()
