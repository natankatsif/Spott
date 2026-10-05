"""The admin panel's sources (Postgres `sources`): one-time import from sites.toml, listing.

    uv run python -m spott.ingest.tools.sources import      # sites.toml → sources (existing sites are kept as they are)
    uv run python -m spott.ingest.tools.sources list

After the import the database is the source of truth: the crawler, the pipeline and the worker read it;
sites.toml stays as the seed for a fresh database.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from spott.core.db import get_connection, init_app_db
from spott.core.paths import SITES_TOML
from spott.core.sources import seed_sources
from spott.ingest.crawler.config import load_sites

from .common import utf8_console


def import_sites(conn: psycopg.Connection, config: Path = SITES_TOML) -> int:
    """sites.toml into `sources` (only the sites not there yet), and the indexed sites without a source;
    returns how many were added. Sites whose robots.txt forbids crawling come in as robots=blocked."""
    return seed_sources(conn, config)


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(prog="python -m spott.ingest.tools.sources", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["import", "list"])
    p.add_argument("--config", type=Path, default=SITES_TOML)
    args = p.parse_args(argv)

    with get_connection(autocommit=True) as conn:
        init_app_db(conn)
        if args.command == "import":
            added = import_sites(conn, args.config)
            print(f"{args.config.name}: {len(load_sites(args.config))} sites, {added} sources added")
        rows = conn.execute("SELECT id, kind, site_id, category, robots, enabled FROM sources ORDER BY id").fetchall()
        if args.command == "list":
            for r in rows:
                print(f"{r[0]:4} {r[1]:8} {r[2]:30} {r[3] or '':16} {r[4]:8} {'on' if r[5] else 'off'}")
        print(f"sources: {len(rows)} ({sum(r[4] == 'blocked' for r in rows)} blocked by robots.txt)")


if __name__ == "__main__":
    main()
