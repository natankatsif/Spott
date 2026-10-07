"""CLI: the first stage of a `check` job — what changed on one site since its last check.

    uv run python -m spott.ingest.freshness --site dgaurf.md --out /tmp/changes.json

Writes {"method", "pages": [...], "documents": [keys], "full_crawl"} for the next stages: the crawler visits
the pages (--start-urls-file), the downloader fetches the documents (--refresh-keys-file). New documents are
registered here. The source row gets the method, the key pages' fingerprints and the time of the check; a site
that didn't answer at all is not recorded as checked. A redesign (most key pages changed at once) queues a full
refresh instead.
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from spott.core.db import get_connection, init_app_db
from spott.ingest.common.http import make_clients
from spott.ingest.common.progress import Progress
from spott.ingest.common.registry import Registry
from spott.ingest.common.urls import bare_host, extension, url_key

from .detect import MAX_KEY_PAGES, detect

log = logging.getLogger("freshness")


def key_pages(start_urls: list[str], site_pages: list) -> list[str]:
    """The start pages, then the site's first-level pages (lists of news, acts, announcements live there)."""
    first_level = sorted((p for p in site_pages if (p["depth"] or 0) <= 1),
                         key=lambda p: ((p["depth"] or 0), p["url"]))
    return list(dict.fromkeys([*start_urls, *(p["url"] for p in first_level)]))[:MAX_KEY_PAGES]


def since_of(source: dict, site_pages: list) -> datetime | None:
    """Changes after the last check; a site never checked: after its last crawl."""
    if source.get("last_checked_at"):
        return source["last_checked_at"]
    return max((p["fetched_at"] for p in site_pages), default=None)


def register_documents(registry: Registry, site: dict, urls: set[str]) -> list[str]:
    """New document URLs into the registry; returns the keys of the ones to (re)download."""
    keys = []
    for url in sorted(urls):
        key = url_key(url)
        registry.add_document(key=key, url=url, site=site["site_id"], category=site.get("category") or "",
                              extension=extension(url),
                              external=bare_host(urlsplit(url).hostname or "") != bare_host(site["site_id"]),
                              source={"found_on": "", "via": "freshness"})
        keys.append(key)
    return keys


def run(site_id: str, out: Path) -> dict:
    progress = Progress(total=1)
    conn = get_connection(autocommit=True)
    init_app_db(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM sources WHERE kind = 'site' AND site_id = %s", (site_id,))
        source = cur.fetchone()
    if source is None:
        raise SystemExit(f"No site source {site_id}")
    registry = Registry.open()
    try:
        pages = registry.site_pages(site_id)
        known = {url_key(p["url"]) for p in pages}
        start = list(source.get("start_urls") or []) or [f"https://{site_id}/"]
        root = f"{urlsplit(start[0]).scheme}://{urlsplit(start[0]).netloc}"

        async def go():
            async with make_clients() as (client, _):
                return await detect(client, root, since=since_of(source, pages), known=known,
                                    key_pages=key_pages(start, pages), stored=dict(source.get("fingerprints") or {}),
                                    delay=float(source.get("delay") or 0.5))

        changes = asyncio.run(go())
        docs = register_documents(registry, source, changes.documents) if changes.reachable else []
    finally:
        registry.close()

    result = {"method": changes.method, "pages": sorted(changes.pages) if changes.reachable else [],
              "documents": docs, "full_crawl": changes.full_crawl and changes.reachable,
              "reachable": changes.reachable}
    if result["full_crawl"]:  # a redesign: the whole site, as its own job, after this one
        conn.execute("INSERT INTO jobs (source_id, kind) SELECT %s, 'refresh' WHERE NOT EXISTS "
                     "(SELECT 1 FROM jobs WHERE source_id = %s AND status = 'queued')", (source["id"], source["id"]))
        result["pages"], result["documents"] = [], []
    if changes.reachable:
        conn.execute("UPDATE sources SET check_method = %s, fingerprints = %s, last_checked_at = %s WHERE id = %s",
                     (changes.method, Jsonb(changes.fingerprints), datetime.now(UTC), source["id"]))
    conn.close()
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    progress.advance()
    progress.finish()
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.freshness", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", required=True, help="site id of the source")
    p.add_argument("--out", type=Path, required=True, help="where to write the changes (JSON)")
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    r = run(args.site, args.out)
    if not r["reachable"]:
        print(f"{args.site}: the site didn't answer; nothing checked")
    elif r["full_crawl"]:
        print(f"{args.site}: most key pages changed at once (a redesign?): a full refresh is queued")
    else:
        print(f"{args.site}: method {r['method']}; changed pages: {len(r['pages'])}; documents: {len(r['documents'])}")
        for url in r["pages"][:20]:
            print(f"  page {url}")


if __name__ == "__main__":
    main()
