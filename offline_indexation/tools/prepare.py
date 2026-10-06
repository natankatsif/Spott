"""Everything the server needs before the API starts (run by the api container, docker/start-api.sh).

    uv run python -m tools.prepare [--dumps /dumps]

1. waits for Postgres, creates the schema;
2. an empty index + a dump in --dumps (the newest *.dump) → restores it;
3. sources from sites.toml (only the new ones);
4. no contacts yet → builds them from the index.
Safe to run on every start: each step does nothing when its work is done.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from retrieval.db import get_connection, init_app_db, init_db

from .common import OI_DIR, utf8_console


def wait_for_db(timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(**_params(), connect_timeout=3):
                return
        except psycopg.OperationalError as e:
            if time.monotonic() > deadline:
                sys.exit(f"Postgres not reachable: {e}")
            time.sleep(2)


def _params() -> dict:
    from retrieval.db import POSTGRES_DB, POSTGRES_HOST, POSTGRES_PASSWORD, POSTGRES_PORT, POSTGRES_USER

    return {"host": POSTGRES_HOST, "port": POSTGRES_PORT, "user": POSTGRES_USER, "password": POSTGRES_PASSWORD,
            "dbname": POSTGRES_DB}


def count(table: str) -> int:
    with get_connection() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(prog="python -m tools.prepare", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dumps", type=Path, default=Path("/dumps"))
    args = p.parse_args(argv)

    print("prepare: waiting for Postgres…", flush=True)
    wait_for_db()
    init_db()
    with get_connection() as conn:
        init_app_db(conn)

    dumps = sorted(args.dumps.glob("*.dump")) if args.dumps.is_dir() else []
    if count("chunks") == 0:
        if dumps:
            print(f"prepare: empty index, restoring {dumps[-1].name}…", flush=True)
            from .index_io import restore_direct

            restore_direct(dumps[-1])
        else:
            print(f"prepare: the index is empty and there is no dump in {args.dumps}: the API will answer not_found. "
                  "Put an index-*.dump there (tools.index_io export on the machine with the index) and restart.",
                  flush=True)
    print(f"prepare: chunks {count('chunks')}", flush=True)

    subprocess.run([sys.executable, "-m", "tools.sources", "import"], cwd=OI_DIR, check=True)
    if count("chunks") and count("contacts") == 0:
        print("prepare: building contacts…", flush=True)
        subprocess.run([sys.executable, "-m", "contacts"], cwd=OI_DIR, check=True)
    print("prepare: done", flush=True)


if __name__ == "__main__":
    main()
