"""Admin job worker: takes queued jobs one at a time and runs the pipeline stages for their source.

    uv run python -m worker          # next to uvicorn; polls for queued jobs
    uv run python -m worker --once   # one job (if any), then exit

Heavy stages never run in parallel: one job at a time per worker, and one worker per machine.
"""

import argparse
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from retrieval.db import get_connection, init_app_db

from tools.common import utf8_console
from tools.pipeline import EXCLUDED_SITES

from . import schedule
from .core import JobRunner

log = logging.getLogger("worker")
REGISTRY = Path(__file__).resolve().parents[1] / "data" / "registry.sqlite"
POLL_S = 2.0
SCHEDULE_EVERY_S = 60.0
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

    def recover_interrupted(self) -> list[int]:
        """Jobs left 'running' by a worker that stopped mid-job (restart, sleep, out of memory). One worker per
        machine, so at start nothing can be running: they are marked failed, so the source can be processed again."""
        rows = self.conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = NOW(), eta_s = NULL, "
            "error = 'Interrupted: the worker stopped during this job. Run it again.' "
            "WHERE status = 'running' RETURNING id").fetchall()
        return [r[0] for r in rows]

    def schedule(self, now: datetime) -> list[tuple[int, str]]:
        """Automatic updates: sets the night slot of new sites, queues the jobs that are due; returns them."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT s.id, s.next_check_at, s.last_full_at,
                       EXISTS (SELECT 1 FROM jobs j WHERE j.source_id = s.id AND j.status IN ('queued', 'running')) AS busy,
                       EXISTS (SELECT 1 FROM jobs j WHERE j.source_id = s.id AND j.status = 'done')
                           OR EXISTS (SELECT 1 FROM chunks c WHERE c.site = s.site_id) AS processed
                FROM sources s
                WHERE s.kind = 'site' AND s.enabled AND s.robots = 'allowed' AND s.auto_update
                ORDER BY s.id""")
            rows = cur.fetchall()
        queue, next_at = schedule.decide([schedule.SourceState(**r) for r in rows], now)
        for source_id, kind in queue:
            self.conn.execute("INSERT INTO jobs (source_id, kind) VALUES (%s, %s)", (source_id, kind))
        for source_id, at in next_at.items():
            self.conn.execute("UPDATE sources SET next_check_at = %s WHERE id = %s", (at, source_id))
        return queue

    def job_done(self, job: dict, status: str) -> None:
        """What a finished job means for its source's automatic updates."""
        if status != "done" or job["source_id"] is None:
            return
        if job["kind"] == "refresh" and not job.get("url"):  # a complete crawl is a check too
            self.conn.execute("UPDATE sources SET last_full_at = NOW(), last_checked_at = NOW(), stale_signals = 0 "
                              "WHERE id = %s", (job["source_id"],))
        elif job["kind"] == "check":
            self.conn.execute("UPDATE sources SET stale_signals = 0 WHERE id = %s", (job["source_id"],))

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
    if interrupted := store.recover_interrupted():
        log.warning("jobs interrupted by a previous worker, marked failed: %s", interrupted)
    runner = JobRunner(store)
    log.info("worker ready, polling for queued jobs%s", "; automatic updates on" if schedule.ENABLED else "")
    scheduled_at = 0.0
    while True:
        if schedule.ENABLED and time.monotonic() - scheduled_at >= SCHEDULE_EVERY_S:
            scheduled_at = time.monotonic()
            try:
                if queued := store.schedule(datetime.now(UTC)):
                    log.info("automatic updates queued: %s", queued)
            except Exception:  # scheduling must never stop the worker
                log.exception("scheduling failed")
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
        store.job_done(job, status)
        log.info("job %s: %s", job["id"], status)
        if args.once:
            return


if __name__ == "__main__":
    main()
