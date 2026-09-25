"""CLI: download documents found by the crawler.

    uv run python -m downloader                          # everything discovered and not downloaded yet
    uv run python -m downloader --sites dgaurf.md --limit 20
    uv run python -m downloader --retry-failed           # try failed downloads again
    uv run python -m downloader --refresh                # re-check downloaded files for updates
"""

import argparse
import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from common.http import make_clients
from common.registry import Registry
from common.urls import bare_host

from .core import Downloader, download_url

log = logging.getLogger("downloader")

OUTCOMES = ("new_file", "duplicate", "updated", "unchanged", "not_modified", "not_a_file", "failed")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m downloader", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/registry.sqlite"))
    p.add_argument("--data", type=Path, default=Path("data"), help="files go to <data>/raw/")
    p.add_argument("--sites", nargs="+", metavar="ID", help="only documents discovered on these sites")
    p.add_argument("--limit", type=int, help="at most N documents")
    p.add_argument("--retry-failed", action="store_true", help="also retry documents that failed before")
    p.add_argument("--refresh", action="store_true",
                   help="also re-check downloaded documents (conditional GET, 304 if unchanged)")
    p.add_argument("--delay", type=float, default=0.5, help="pause between requests to one host, seconds")
    p.add_argument("--concurrency", type=int, default=6, help="hosts downloaded from in parallel")
    return p.parse_args()


async def download_all(args: argparse.Namespace, registry: Registry) -> Downloader | None:
    statuses = ["discovered"]
    if args.retry_failed:
        statuses.append("failed")
    if args.refresh:
        statuses.append("downloaded")
    docs = registry.documents_to_download(statuses, args.sites, args.limit)
    if not docs:
        print("Nothing to download.")
        return None

    by_host = defaultdict(list)
    for doc in docs:
        by_host[bare_host(urlsplit(download_url(doc["url"])).hostname or "")].append(doc)
    log.info("%d documents from %d hosts", len(docs), len(by_host))

    semaphore = asyncio.Semaphore(args.concurrency)
    async with make_clients() as (client, insecure_client):
        downloader = Downloader(client, insecure_client, registry, args.data,
                                delay=args.delay, refresh=args.refresh)

        async def one_host(host, host_docs):
            async with semaphore:
                await downloader.download_host(host, host_docs)

        await asyncio.gather(*(one_host(h, d) for h, d in by_host.items()))
    return downloader


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    registry = Registry(args.db)
    try:
        downloader = asyncio.run(download_all(args, registry))
        if downloader:
            print("\nThis run:")
            for outcome in OUTCOMES:
                print(f"  {outcome:14} {downloader.stats[outcome]}")
        counts = registry.status_counts()
        print("\nRegistry:")
        print("  documents:", ", ".join(f"{k}={v}" for k, v in counts["documents"].items()) or "—")
        print("  files:    ", ", ".join(f"{k}={v}" for k, v in counts["files"].items()) or "—")
    finally:
        registry.close()


if __name__ == "__main__":
    main()
