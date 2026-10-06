"""Admin job worker: takes queued jobs one at a time and runs the pipeline stages for their source.

    uv run python -m spott.ingest.worker          # next to uvicorn; polls for queued jobs
    uv run python -m spott.ingest.worker --once   # one job (if any), then exit

Heavy stages never run in parallel: one job at a time per worker, and one worker per machine.
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from spott.core.db import get_connection, init_app_db
from spott.core.paths import REGISTRY
from spott.ingest.tools.common import utf8_console
from spott.ingest.tools.pipeline import EXCLUDED_SITES

from . import schedule
from .core import JobRunner

log = logging.getLogger("worker")
POLL_S = 2.0
SCHEDULE_EVERY_S = 60.0
BACKLOG_EVERY_S = 30.0
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

    def queue_due(self, now: datetime) -> list[tuple[int, str]]:
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

    def backlog_candidates(self) -> list[schedule.SiteWork]:
        """How much each source still has left to do. Sources with a job of their own queued or running are left
        alone, and so is one whose last backlog job failed in the past hour: something is wrong with it and the
        autopilot must not spin on it while other sources wait."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT s.id, s.site_id FROM sources s
                WHERE s.kind = 'site' AND s.enabled AND s.robots = 'allowed' AND s.auto_update
                  AND NOT EXISTS (SELECT 1 FROM jobs j
                                  WHERE j.source_id = s.id AND j.status IN ('queued', 'running'))
                  AND NOT EXISTS (SELECT 1 FROM jobs j
                                  WHERE j.source_id = s.id AND j.kind = 'backlog' AND j.status = 'failed'
                                    AND j.finished_at > NOW() - INTERVAL '1 hour')
                ORDER BY s.id""")
            rows = [r for r in cur.fetchall() if r["site_id"] not in EXCLUDED_SITES]
        if not rows or not self.registry.exists():
            return []
        with sqlite3.connect(f"file:{self.registry}?mode=ro", uri=True) as db:
            docs = {site: (undownloaded, pending) for site, undownloaded, pending in db.execute("""
                SELECT d.site, COALESCE(SUM(d.status = 'discovered'), 0),
                       COUNT(DISTINCT CASE WHEN f.parse_status = 'pending' THEN f.sha256 END)
                FROM documents d LEFT JOIN files f ON f.sha256 = d.sha256
                WHERE d.status IN ('discovered', 'downloaded') GROUP BY d.site""")}
            pages = dict(db.execute("SELECT site, COUNT(*) FROM pages WHERE parse_status = 'pending' "
                                    "AND status < 400 AND html_file IS NOT NULL GROUP BY site").fetchall())
        works = []
        for row in rows:
            undownloaded, files_pending = docs.get(row["site_id"], (0, 0))
            works.append(schedule.SiteWork(
                id=row["id"], site_id=row["site_id"], queue_left=self.crawl_queue_left(row["site_id"]),
                undownloaded=undownloaded, files_pending=files_pending,
                pages_pending=pages.get(row["site_id"], 0)))
        return works

    def crawl_queue_left(self, site_id: str) -> int:
        """Pages the last crawl of this site saved and never got to; 0 when it finished or never ran."""
        state = self.registry.parent / "crawl" / site_id / "state.json"
        try:
            return len(json.loads(state.read_text(encoding="utf-8")).get("queue") or [])
        except (OSError, ValueError):
            return 0

    def queue_backlog(self) -> schedule.SiteWork | None:
        """One backlog job for the source with the most work left, and only while nothing else is waiting: the
        admin's own jobs and the nightly checks always go first."""
        if self.conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued', 'running') LIMIT 1").fetchone():
            return None
        work = schedule.next_backlog(self.backlog_candidates())
        if work is None:
            return None
        self.conn.execute("INSERT INTO jobs (source_id, kind) VALUES (%s, 'backlog')", (work.id,))
        return work

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
    p = argparse.ArgumentParser(prog="python -m spott.ingest.worker", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="run at most one job, then exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    store = PgJobStore()
    if interrupted := store.recover_interrupted():
        log.warning("jobs interrupted by a previous worker, marked failed: %s", interrupted)
    runner = JobRunner(store)
    log.info("worker ready, polling for queued jobs%s%s", "; automatic updates on" if schedule.ENABLED else "",
             "; autopilot on" if schedule.BACKLOG else "")
    scheduled_at = backlog_at = 0.0
    while True:
        if schedule.ENABLED and time.monotonic() - scheduled_at >= SCHEDULE_EVERY_S:
            scheduled_at = time.monotonic()
            try:
                if queued := store.queue_due(datetime.now(UTC)):
                    log.info("automatic updates queued: %s", queued)
            except Exception:  # scheduling must never stop the worker
                log.exception("scheduling failed")
        if schedule.BACKLOG and time.monotonic() - backlog_at >= BACKLOG_EVERY_S:
            backlog_at = time.monotonic()
            try:
                if work := store.queue_backlog():
                    log.info("autopilot: %s queued (%s)", work.site_id, work.summary())
            except Exception:  # the autopilot must never stop the worker
                log.exception("queueing a backlog job failed")
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
