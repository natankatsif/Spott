"""The admin panel's sources (Postgres `sources`): one-time import from sites.toml, listing.

    uv run python -m tools.sources import      # sites.toml → sources (existing sites are kept as they are)
    uv run python -m tools.sources list

After the import the database is the source of truth: the crawler, the pipeline and the worker read it;
sites.toml stays as the seed for a fresh database.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from retrieval.db import get_connection, init_app_db

from crawler.config import Site, load_sites

from .common import OI_DIR, utf8_console
from .pipeline import EXCLUDED_SITES

SITES_TOML = OI_DIR / "data" / "sources" / "sites.toml"

INSERT_SITE = """
INSERT INTO sources (kind, url, site_id, category, start_urls, max_depth, max_pages, delay, robots)
VALUES ('site', %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (site_id) WHERE kind = 'site' DO NOTHING
"""


def import_sites(conn: psycopg.Connection, sites: list[Site]) -> int:
    """Adds the sites not in `sources` yet; returns how many were added. Sites whose robots.txt forbids
    crawling (EXCLUDED_SITES) come in as robots=blocked."""
    added = 0
    with conn.cursor() as cur:
        for s in sites:
            cur.execute(INSERT_SITE, (s.start_urls[0], s.id, s.category, Jsonb(s.start_urls), s.max_depth,
                                      s.max_pages, s.delay, "blocked" if s.id in EXCLUDED_SITES else "allowed"))
            added += cur.rowcount
    return added


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(prog="python -m tools.sources", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["import", "list"])
    p.add_argument("--config", type=Path, default=SITES_TOML)
    args = p.parse_args(argv)

    with get_connection(autocommit=True) as conn:
        init_app_db(conn)
        if args.command == "import":
            sites = load_sites(args.config)
            added = import_sites(conn, sites)
            print(f"{args.config.name}: {len(sites)} sites, {added} added, {len(sites) - added} already there")
        rows = conn.execute("SELECT id, kind, site_id, category, robots, enabled FROM sources ORDER BY id").fetchall()
        if args.command == "list":
            for r in rows:
                print(f"{r[0]:4} {r[1]:8} {r[2]:30} {r[3] or '':16} {r[4]:8} {'on' if r[5] else 'off'}")
        print(f"sources: {len(rows)} ({sum(r[4] == 'blocked' for r in rows)} blocked by robots.txt)")


if __name__ == "__main__":
    main()
