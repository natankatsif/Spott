"""Admin job worker: takes queued jobs one at a time and runs the pipeline stages for their source.

    uv run python -m worker          # next to uvicorn; polls for queued jobs
    uv run python -m worker --once   # one job (if any), then exit

Heavy stages never run in parallel: one job at a time per worker, and one worker per machine.
"""

import argparse
import logging
import sqlite3
import time
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from retrieval.db import get_connection, init_app_db

from tools.common import utf8_console
from tools.pipeline import EXCLUDED_SITES

from .core import JobRunner

log = logging.getLogger("worker")
REGISTRY = Path(__file__).resolve().parents[1] / "data" / "registry.sqlite"
POLL_S = 2.0
JSON_FIELDS = {"stats", "log_tail"}


class PgJobStore:
    def __init__(self, registry: Path = REGISTRY):
        self.conn = get_connection(autocommit=True)
        init_app_db(self.conn)
        self.registry = registry

    def claim(self) -> dict | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE jobs SET status = 'running', started_at = NOW()
                WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING *""")
            return cur.fetchone()

    def source(self, source_id: int | None) -> dict | None:
        if source_id is None:
            return None
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM sources WHERE id = %s", (source_id,))
            return cur.fetchone()

    def all_sites(self) -> list[str]:
        rows = self.conn.execute("SELECT site_id FROM sources WHERE kind = 'site' AND enabled AND robots = 'allowed' "
                                 "ORDER BY id").fetchall()
        return [r[0] for r in rows if r[0] not in EXCLUDED_SITES]

    def update_job(self, job_id: int, **fields) -> None:
        finished = fields.pop("finished", False)
        sets = [f"{k} = %s" for k in fields] + (["finished_at = NOW()"] if finished else [])
        values = [Jsonb(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = %s", (*values, job_id))

    def cancel_requested(self, job_id: int) -> bool:
        row = self.conn.execute("SELECT cancel_requested FROM jobs WHERE id = %s", (job_id,)).fetchone()
        return bool(row and row[0])

    def site_stats(self, site_id: str) -> dict[str, int]:
        """What the site has after the job: registry counts (pages, documents) and the index (chunks, lines)."""
        stats: dict[str, int] = {}
        if self.registry.exists():
            with sqlite3.connect(f"file:{self.registry}?mode=ro", uri=True) as db:
                stats["pages"] = db.execute("SELECT COUNT(*) FROM pages WHERE site = ?", (site_id,)).fetchone()[0]
                found, downloaded = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(status = 'downloaded'), 0) FROM documents WHERE site = ?",
                    (site_id,)).fetchone()
                parsed = db.execute(
                    "SELECT COUNT(DISTINCT f.sha256) FROM files f JOIN documents d ON d.sha256 = f.sha256 "
                    "WHERE d.site = ? AND f.parse_status = 'parsed'", (site_id,)).fetchone()[0]
                stats |= {"documents_found": found, "documents_downloaded": downloaded, "files_parsed": parsed}
        chunks, lines = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM chunks WHERE site = %s), "
            "(SELECT COUNT(*) FROM lines l JOIN chunks c ON c.chunk_id = l.chunk_id WHERE c.site = %s)",
            (site_id, site_id)).fetchone()
        return stats | {"chunks": chunks, "lines": lines}


def main() -> None:
    utf8_console()
    p = argparse.ArgumentParser(prog="python -m worker", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="run at most one job, then exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    store = PgJobStore()
    runner = JobRunner(store)
    log.info("worker ready, polling for queued jobs")
    while True:
        job = store.claim()
        if job is None:
            if args.once:
                return
            time.sleep(POLL_S)
            continue
        source = store.source(job["source_id"])
        log.info("job %s: %s %s", job["id"], job["kind"], source["site_id"] if source else "all sources")
        try:
            status = runner.run(job, source, store.all_sites() if source is None else None)
        except Exception as e:
            log.exception("job %s failed", job["id"])
            store.update_job(job["id"], status="failed", error=f"{type(e).__name__}: {e}", finished=True)
            status = "failed"
        log.info("job %s: %s", job["id"], status)
        if args.once:
            return


if __name__ == "__main__":
    main()
