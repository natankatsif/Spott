"""Admin panel API (docs/API.md → Admin): sources in Postgres, crawl jobs run by `python -m worker`, ratings.

Login: POST /api/admin/login with ADMIN_LOGIN / ADMIN_PASSWORD from the server's env gives a session token
(12 h, signed, nothing stored); every other endpoint needs `Authorization: Bearer <token>`. Without ADMIN_LOGIN and
ADMIN_PASSWORD on the server the admin is off: everything answers 401.
The worker process (offline_indexation/worker) takes the queued jobs and writes their progress; this API only
queues, reads and cancels them.
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import psycopg
from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from .errors import ApiException, RateLimiter
from .schemas import (
    AdminLogin,
    AdminMe,
    AdminSession,
    FeedbackItem,
    FeedbackList,
    FeedbackStats,
    Job,
    JobCreate,
    JobList,
    SourceCreate,
    SourceList,
    SourcePatch,
    SourceRow,
    Suggestion,
    SuggestionCreate,
)

# The crawler's User-Agent (offline_indexation/common/http.py): robots.txt is checked for the bot that will crawl.
CRAWLER_AGENT = "ChisinauAssistantBot/0.1 (+GigaHack 2026; municipal RAG research crawler)"
DOCUMENT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


SESSION_HOURS = float(os.getenv("ADMIN_SESSION_HOURS", "12"))
LOGIN_ATTEMPTS_PER_MINUTE = 5
login_limiter = RateLimiter(limit=LOGIN_ATTEMPTS_PER_MINUTE)


def credentials() -> tuple[str, str] | None:
    login, password = os.getenv("ADMIN_LOGIN", ""), os.getenv("ADMIN_PASSWORD", "")
    return (login, password) if login and password else None


def signing_key(login: str, password: str) -> bytes:
    """ADMIN_SECRET if set; else derived from the credentials, so changing the password ends every session."""
    secret = os.getenv("ADMIN_SECRET") or f"{login}\0{password}"
    return hashlib.sha256(secret.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(login: str, expires: datetime) -> str:
    creds = credentials()
    assert creds is not None
    payload = _b64(json.dumps({"sub": login, "exp": int(expires.timestamp())}).encode())
    signature = _b64(hmac.new(signing_key(*creds), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def check_token(token: str) -> str | None:
    """The login of a valid, unexpired token signed with the current credentials; None otherwise."""
    creds = credentials()
    if creds is None or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    expected = _b64(hmac.new(signing_key(*creds), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if claims.get("sub") != creds[0] or claims.get("exp", 0) < datetime.now(UTC).timestamp():
        return None
    return claims["sub"]


def require_admin(request: Request) -> str:
    header = request.headers.get("authorization", "")
    login = check_token(header.removeprefix("Bearer ").strip()) if header.startswith("Bearer ") else None
    if login is None:
        raise ApiException(401, "unauthorized", "Admin session required: POST /api/admin/login, then "
                                                 "Authorization: Bearer <token>")
    return login


auth_router = APIRouter(prefix="/api/admin")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@auth_router.post("/login", response_model=AdminSession)
def login(req: AdminLogin, request: Request) -> AdminSession:
    forwarded = request.headers.get("x-forwarded-for", "")
    login_limiter.check(forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown"))
    creds = credentials()
    if creds is None:
        raise ApiException(401, "unauthorized", "Admin is off: ADMIN_LOGIN / ADMIN_PASSWORD not set on the server")
    ok_login = hmac.compare_digest(req.login.encode(), creds[0].encode())
    ok_password = hmac.compare_digest(req.password.encode(), creds[1].encode())
    if not (ok_login and ok_password):
        raise ApiException(401, "unauthorized", "Wrong login or password")
    expires = datetime.now(UTC) + timedelta(hours=SESSION_HOURS)
    return AdminSession(token=make_token(creds[0], expires), login=creds[0],
                        expires_at=expires.isoformat(timespec="seconds"))


@router.get("/me", response_model=AdminMe)
def me(login: str = Depends(require_admin)) -> AdminMe:
    """Whether the stored token is still valid (the UI checks it on load)."""
    return AdminMe(login=login)


# ─────────────── checks when a source is added ───────────────


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiException(422, "validation_error", "url: only http(s) URLs with a host")
    return site_of(url)


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether robots.txt lets the crawler fetch this URL. No robots.txt (4xx) allows; a site that can't be reached
    at all counts as allowed here — the crawler checks robots.txt again before every crawl."""
    parts = urlsplit(url)
    try:
        resp = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10.0)
    except httpx.HTTPError:
        return True
    if resp.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(CRAWLER_AGENT, url)


async def document_type(client: httpx.AsyncClient, url: str) -> str | None:
    """The extension of a PDF / DOC / DOCX answer to this URL, None for anything else."""
    try:
        resp = await client.head(url, timeout=15.0)
        if resp.status_code >= 400:  # some servers don't answer HEAD: read only the headers of a GET
            async with client.stream("GET", url, timeout=15.0) as resp:
                pass
    except httpx.HTTPError as e:
        raise ApiException(422, "validation_error", f"url: can't be fetched ({type(e).__name__})") from e
    if resp.status_code >= 400:
        raise ApiException(422, "validation_error", f"url: the site answered {resp.status_code}")
    return DOCUMENT_TYPES.get(resp.headers.get("content-type", "").split(";")[0].strip().lower())


# ─────────────── storage ───────────────


class Duplicate(Exception):
    pass


class AdminStore(Protocol):
    def list_sources(self) -> list[dict]: ...
    def get_source(self, source_id: int) -> dict | None: ...
    def add_source(self, row: dict) -> dict: ...
    def patch_source(self, source_id: int, fields: dict) -> dict | None: ...
    def delete_source(self, source_id: int, purge: bool) -> bool: ...
    def create_job(self, source_id: int | None, kind: str) -> dict: ...
    def list_jobs(self, status: str | None) -> list[dict]: ...
    def get_job(self, job_id: int) -> dict | None: ...
    def cancel_job(self, job_id: int) -> dict | None: ...
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
        rows = self._rows(
            f"""
            SELECT s.*, COALESCE(n.chunks, 0) AS chunks, to_jsonb(j) AS last_job
            FROM sources s
            LEFT JOIN (SELECT site, COUNT(*) AS chunks FROM chunks GROUP BY site) n ON n.site = s.site_id
            LEFT JOIN LATERAL (SELECT {JOB_COLUMNS} FROM jobs WHERE jobs.id = s.last_job_id) j ON TRUE
            ORDER BY s.id
            """)
        return [with_job(r) for r in rows]

    def get_source(self, source_id: int) -> dict | None:
        return next((r for r in self.list_sources() if r["id"] == source_id), None)

    def add_source(self, row: dict) -> dict:
        try:
            [created] = self._rows(
                "INSERT INTO sources (kind, url, site_id, category, start_urls, max_depth, max_pages, robots) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (row["kind"], row["url"], row["site_id"], row.get("category"), Jsonb(row["start_urls"]),
                 row.get("max_depth"), row.get("max_pages"), row["robots"]))
        except psycopg.errors.UniqueViolation as e:
            raise Duplicate from e
        return self.get_source(created["id"])

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

    def create_job(self, source_id: int | None, kind: str) -> dict:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"INSERT INTO jobs (source_id, kind) VALUES (%s, %s) RETURNING {JOB_COLUMNS}",
                        (source_id, kind))
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


def iso(value: Any) -> str | None:
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else value


def with_job(row: dict) -> dict:
    job = row.get("last_job")
    return row | {"last_job": job if job and job.get("id") is not None else None}


def job_model(row: dict) -> Job:
    stats = row.get("stats") or {}
    return Job(**{k: row.get(k) for k in Job.model_fields if k not in ("started_at", "finished_at", "stats",
                                                                         "log_tail")},
               started_at=iso(row.get("started_at")), finished_at=iso(row.get("finished_at")),
               stats=json.loads(stats) if isinstance(stats, str) else stats,
               log_tail=list(row.get("log_tail") or []))


def source_model(row: dict) -> SourceRow:
    job = row.get("last_job")
    return SourceRow(id=row["id"], kind=row["kind"], url=row["url"], site_id=row["site_id"],
                     category=row.get("category"), start_urls=list(row.get("start_urls") or []),
                     max_depth=row.get("max_depth"), max_pages=row.get("max_pages"), enabled=row["enabled"],
                     robots=row["robots"], created_at=iso(row["created_at"]), chunks=row.get("chunks", 0),
                     last_job=job_model(job) if job else None)


def store(request: Request) -> AdminStore:
    admin = getattr(request.app.state, "admin", None)
    if admin is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return admin


def http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


# ─────────────── sources and jobs ───────────────


@router.get("/sources", response_model=SourceList)
async def list_sources(request: Request) -> SourceList:
    rows = await run_in_threadpool(store(request).list_sources)
    return SourceList(sources=[source_model(r) for r in rows])


@router.post("/sources", response_model=SourceRow, status_code=201)
async def add_source(req: SourceCreate, request: Request) -> SourceRow:
    site_id = check_url(req.url)
    client = http_client(request)
    if req.kind == "document" and await document_type(client, req.url) is None:
        raise ApiException(422, "validation_error", "url: not a PDF, DOC or DOCX document")
    robots = "allowed" if await robots_allowed(client, req.url) else "blocked"
    row = {"kind": req.kind, "url": req.url, "site_id": site_id, "category": req.category,
           "start_urls": [req.url] if req.kind == "site" else [], "max_depth": req.max_depth,
           "max_pages": req.max_pages, "robots": robots}
    admin = store(request)
    try:
        created = await run_in_threadpool(admin.add_source, row)
    except Duplicate as e:
        what = f"site {site_id}" if req.kind == "site" else "document"
        raise ApiException(409, "conflict", f"This {what} is already a source") from e
    if req.start and robots == "allowed":
        await run_in_threadpool(admin.create_job, created["id"], "crawl")
        created = await run_in_threadpool(admin.get_source, created["id"])
    return source_model(created)


@router.patch("/sources/{source_id}", response_model=SourceRow)
async def patch_source(source_id: int, req: SourcePatch, request: Request) -> SourceRow:
    row = await run_in_threadpool(store(request).patch_source, source_id, req.model_dump(exclude_none=True))
    if row is None:
        raise ApiException(404, "not_found", f"No source {source_id}")
    return source_model(row)


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, request: Request, purge: bool = False) -> dict:
    if not await run_in_threadpool(store(request).delete_source, source_id, purge):
        raise ApiException(404, "not_found", f"No source {source_id}")
    return {"ok": True}


@router.post("/sources/{source_id}/jobs", response_model=Job, status_code=201)
async def start_job(source_id: int, req: JobCreate, request: Request) -> Job:
    admin = store(request)
    source = await run_in_threadpool(admin.get_source, source_id)
    if source is None:
        raise ApiException(404, "not_found", f"No source {source_id}")
    if source["robots"] == "blocked":
        raise ApiException(409, "conflict", f"robots.txt of {source['site_id']} forbids crawling")
    if not source["enabled"]:
        raise ApiException(409, "conflict", "The source is disabled")
    job = source.get("last_job")
    if job and job["status"] in ("queued", "running"):
        raise ApiException(409, "conflict", f"Job {job['id']} of this source is still {job['status']}")
    return job_model(await run_in_threadpool(admin.create_job, source_id, req.kind))


@router.get("/jobs", response_model=JobList)
async def list_jobs(request: Request, status: str | None = None) -> JobList:
    return JobList(jobs=[job_model(j) for j in await run_in_threadpool(store(request).list_jobs, status)])


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: int, request: Request) -> Job:
    job = await run_in_threadpool(store(request).get_job, job_id)
    if job is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    return job_model(job)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: int, request: Request) -> Job:
    job = await run_in_threadpool(store(request).cancel_job, job_id)
    if job is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    return job_model(job)


# ─────────────── ratings ───────────────


@router.get("/feedback", response_model=FeedbackList)
async def low_rated(request: Request, max_rating: int = Query(2, ge=1, le=5),
                    limit: int = Query(50, ge=1, le=500)) -> FeedbackList:
    rows = await run_in_threadpool(store(request).feedback, max_rating, limit)
    return FeedbackList(items=[FeedbackItem(**{k: iso(r.get(k)) for k in FeedbackItem.model_fields}) for r in rows])


@router.get("/feedback/stats", response_model=FeedbackStats)
async def feedback_stats(request: Request) -> FeedbackStats:
    return FeedbackStats(**await run_in_threadpool(store(request).feedback_stats))


# ─────────────── quick questions ───────────────


@router.post("/suggestions", response_model=Suggestion, status_code=201)
async def pin_suggestion(req: SuggestionCreate, request: Request) -> Suggestion:
    """A question the admin wants among the quick questions; shown once the next re-check answers it well."""
    suggestions = getattr(request.app.state, "suggestions", None)
    if suggestions is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return await run_in_threadpool(suggestions.add, req.question, req.lang, req.pinned)


@router.delete("/suggestions/{suggestion_id}")
async def hide_suggestion(suggestion_id: int, request: Request) -> dict:
    suggestions = getattr(request.app.state, "suggestions", None)
    if suggestions is None or not await run_in_threadpool(suggestions.hide, suggestion_id):
        raise ApiException(404, "not_found", f"No quick question {suggestion_id}")
    return {"ok": True}
