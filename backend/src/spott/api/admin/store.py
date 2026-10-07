"""The admin's sources and jobs in Postgres (the worker, spott.ingest.worker, runs the jobs; this only queues, reads
and cancels them), and the ratings' numbers."""

from typing import Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..stats import corpus_totals, registry_counts


class Duplicate(Exception):
    pass


class AdminStore(Protocol):
    def list_sources(self) -> list[dict]: ...
    def get_source(self, source_id: int) -> dict | None: ...
    def find_by_site(self, site_id: str) -> dict | None: ...
    def add_source(self, row: dict) -> dict: ...
    def merge_url(self, source_id: int, url: str) -> dict | None: ...
    def totals(self) -> dict: ...
    def patch_source(self, source_id: int, fields: dict) -> dict | None: ...
    def delete_source(self, source_id: int, purge: bool) -> bool: ...
    def create_job(self, source_id: int | None, kind: str, url: str | None = None) -> dict: ...
    def list_jobs(self, status: str | None) -> list[dict]: ...
    def get_job(self, job_id: int) -> dict | None: ...
    def cancel_job(self, job_id: int) -> dict | None: ...
    def retry_job(self, job_id: int) -> dict | None: ...
    def delete_job(self, job_id: int) -> bool | None: ...
    def clear_jobs(self) -> int: ...
    def feedback(self, max_rating: int, limit: int) -> list[dict]: ...
    def feedback_stats(self) -> dict: ...


JOB_COLUMNS = ("id, source_id, kind, status, stage, stage_done, stage_total, percent, eta_s, started_at, "
               "finished_at, stats, log_tail, error")


class PgAdminStore:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def list_sources(self) -> list[dict]:
        """Every source with its index counters (chunks, lines), the registry's (pages, documents, last crawl)
        and its last job."""
        rows = self._rows(
            f"""
            SELECT s.*, to_jsonb(j) AS last_job,
                   CASE WHEN s.kind = 'site' THEN COALESCE(n.chunks, 0) ELSE COALESCE(d.chunks, 0) END AS chunks,
                   CASE WHEN s.kind = 'site' THEN COALESCE(n.lines, 0) ELSE COALESCE(d.lines, 0) END AS lines
            FROM sources s
            LEFT JOIN (SELECT c.site, COUNT(*) AS chunks, SUM(l.n) AS lines FROM chunks c
                       LEFT JOIN (SELECT chunk_id, COUNT(*) AS n FROM lines GROUP BY chunk_id) l USING (chunk_id)
                       GROUP BY c.site) n ON n.site = s.site_id
            LEFT JOIN LATERAL (SELECT COUNT(*) AS chunks,
                                      (SELECT COUNT(*) FROM lines l JOIN chunks c2 USING (chunk_id)
                                       WHERE c2.url = s.url) AS lines
                               FROM chunks c WHERE s.kind = 'document' AND c.url = s.url) d ON TRUE
            LEFT JOIN LATERAL (SELECT {JOB_COLUMNS} FROM jobs WHERE jobs.id = s.last_job_id) j ON TRUE
            ORDER BY s.id
            """)
        with self.pool.connection() as conn:
            registry = registry_counts(conn)
        return [with_job(r) | registry_fields(r, registry) for r in rows]

    def get_source(self, source_id: int) -> dict | None:
        return next((r for r in self.list_sources() if r["id"] == source_id), None)

    def find_by_site(self, site_id: str) -> dict | None:
        rows = self._rows("SELECT id FROM sources WHERE site_id = %s ORDER BY (kind = 'site') DESC, id LIMIT 1",
                          (site_id,))
        return self.get_source(rows[0]["id"]) if rows else None

    def add_source(self, row: dict) -> dict:
        try:
            [created] = self._rows(
                "INSERT INTO sources (kind, url, site_id, title, category, category_source, start_urls, max_depth, "
                "max_pages, robots) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (row["kind"], row["url"], row["site_id"], row.get("title"), row.get("category"),
                 row.get("category_source"), Jsonb(row["start_urls"]), row.get("max_depth"), row.get("max_pages"),
                 row["robots"]))
        except psycopg.errors.UniqueViolation as e:
            raise Duplicate from e
        return self.get_source(created["id"])

    def merge_url(self, source_id: int, url: str) -> dict | None:
        """A deeper path or a document of the source's domain: into its start URLs (crawled from then on)."""
        self._rows("UPDATE sources SET start_urls = start_urls || %s WHERE id = %s AND NOT start_urls ? %s",
                   (Jsonb([url]), source_id, url))
        return self.get_source(source_id)

    def totals(self) -> dict:
        return corpus_totals(self.pool).model_dump()

    def patch_source(self, source_id: int, fields: dict) -> dict | None:
        if fields:
            sets = ", ".join(f"{k} = %s" for k in fields)
            self._rows(f"UPDATE sources SET {sets} WHERE id = %s", (*fields.values(), source_id))
        return self.get_source(source_id)

    def delete_source(self, source_id: int, purge: bool) -> bool:
        source = self.get_source(source_id)
        if source is None:
            return False
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            if purge:  # chunks and lines go with their documents (ON DELETE CASCADE)
                if source["kind"] == "site":
                    cur.execute("DELETE FROM documents WHERE site = %s", (source["site_id"],))
                else:
                    cur.execute("DELETE FROM documents WHERE url = %s", (source["url"],))
            cur.execute("UPDATE jobs SET cancel_requested = TRUE WHERE source_id = %s AND status = 'running'",
                        (source_id,))
            cur.execute("UPDATE jobs SET status = 'cancelled', finished_at = NOW() "
                        "WHERE source_id = %s AND status = 'queued'", (source_id,))
            cur.execute("DELETE FROM sources WHERE id = %s", (source_id,))
        return True

    def create_job(self, source_id: int | None, kind: str, url: str | None = None) -> dict:
        """url: only this link of the source (a deeper path crawled under its prefix, or one document)."""
        with self.pool.connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"INSERT INTO jobs (source_id, kind, url) VALUES (%s, %s, %s) RETURNING {JOB_COLUMNS}",
                        (source_id, kind, url))
            job = cur.fetchone()
            if source_id is not None:
                cur.execute("UPDATE sources SET last_job_id = %s WHERE id = %s", (job["id"], source_id))
        return job

    def list_jobs(self, status: str | None) -> list[dict]:
        where, params = ("WHERE status = %s", (status,)) if status else ("", ())
        return self._rows(f"SELECT {JOB_COLUMNS} FROM jobs {where} ORDER BY id DESC LIMIT 100", params)

    def get_job(self, job_id: int) -> dict | None:
        rows = self._rows(f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = %s", (job_id,))
        return rows[0] if rows else None

    def cancel_job(self, job_id: int) -> dict | None:
        """A queued job is cancelled at once; a running one when the worker next looks (after the current item)."""
        self._rows("UPDATE jobs SET status = 'cancelled', finished_at = NOW() WHERE id = %s AND status = 'queued'",
                   (job_id,))
        self._rows("UPDATE jobs SET cancel_requested = TRUE WHERE id = %s AND status = 'running'", (job_id,))
        return self.get_job(job_id)

    def retry_job(self, job_id: int) -> dict | None:
        """The same work again (source, kind, link) as a new queued job; None if there is no such job."""
        rows = self._rows("SELECT source_id, kind, url FROM jobs WHERE id = %s", (job_id,))
        return self.create_job(rows[0]["source_id"], rows[0]["kind"], rows[0]["url"]) if rows else None

    def delete_job(self, job_id: int) -> bool | None:
        """Removes a finished job from the history; None if it doesn't exist, False while it is queued or running."""
        rows = self._rows("SELECT status FROM jobs WHERE id = %s", (job_id,))
        if not rows:
            return None
        if rows[0]["status"] in ("queued", "running"):
            return False
        self._rows("UPDATE sources SET last_job_id = NULL WHERE last_job_id = %s", (job_id,))
        self._rows("DELETE FROM jobs WHERE id = %s", (job_id,))
        return True

    def clear_jobs(self) -> int:
        """Removes every finished job (done, failed, cancelled); returns how many."""
        self._rows("UPDATE sources SET last_job_id = NULL WHERE last_job_id IN "
                   "(SELECT id FROM jobs WHERE status NOT IN ('queued', 'running'))")
        return len(self._rows("DELETE FROM jobs WHERE status NOT IN ('queued', 'running') RETURNING id"))

    def feedback(self, max_rating: int, limit: int) -> list[dict]:
        return self._rows("SELECT * FROM feedback WHERE rating <= %s ORDER BY rating, updated_at DESC LIMIT %s",
                          (max_rating, limit))

    def feedback_stats(self) -> dict:
        [total] = self._rows("SELECT COUNT(*) AS count, AVG(rating)::float AS average FROM feedback")
        stars = {str(r["rating"]): r["n"] for r in self._rows(
            "SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating")}
        tags = self._rows("SELECT t AS tag, COUNT(*) AS count FROM feedback, jsonb_array_elements_text(tags) t "
                          "GROUP BY t ORDER BY count DESC, t LIMIT 6")
        days = self._rows("SELECT to_char(updated_at, 'YYYY-MM-DD') AS day, COUNT(*) AS count, "
                          "AVG(rating)::float AS average FROM feedback GROUP BY 1 ORDER BY 1")
        return {"count": total["count"], "average": total["average"],
                "per_star": {str(n): stars.get(str(n), 0) for n in range(1, 6)}, "top_tags": tags, "by_day": days}


def with_job(row: dict) -> dict:
    job = row.get("last_job")
    return row | {"last_job": job if job and job.get("id") is not None else None}


def registry_fields(row: dict, registry: dict[str, dict]) -> dict:
    """Pages, documents and last crawl of a site source from the registry."""
    reg = registry.get(row["site_id"], {}) if row["kind"] == "site" else {}
    return {"pages": reg.get("pages", 0), "documents_found": reg.get("documents_found", 0),
            "documents_downloaded": reg.get("documents_downloaded", 0), "last_crawled": reg.get("last_crawled"),
            **{k: reg.get(k, 0) for k in ("crawl_left", "documents_pending", "files_pending", "pages_pending")}}
