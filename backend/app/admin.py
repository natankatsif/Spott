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
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from retrieval.sources import DEFAULTS, EXCLUDED_SITES, categorize
from selectolax.parser import HTMLParser
from starlette.concurrency import run_in_threadpool

from .errors import ApiException, RateLimiter, client_address
from .schemas import (
    AdminLogin,
    AdminMe,
    AdminSession,
    CorpusTotals,
    FeedbackItem,
    FeedbackList,
    FeedbackStats,
    GapList,
    GapRecheck,
    Job,
    JobCreate,
    JobList,
    SourceAdded,
    SourceCreate,
    SourceDetected,
    SourceList,
    SourcePatch,
    SourceProgress,
    SourceRow,
    Suggestion,
    SuggestionCreate,
    SuggestionList,
)
from .stats import REGISTRY, corpus_stats, registry_counts

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
    login_limiter.check(client_address(request))
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


# ─────────────── what a URL is: robots.txt, kind, title, category (no LLM) ───────────────

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx")
PROBE_TIMEOUT_S = 8.0
MAX_HEAD_BYTES = 300_000  # enough of a page for its <title> and meta description


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def normalize_url(url: str) -> str:
    """Scheme added when missing, host lowercased, a trailing slash dropped (except the root)."""
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise ApiException(422, "validation_error", "url: only http(s) links to a website or a document")
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def check_url(url: str) -> str:
    return site_of(normalize_url(url))


def is_root(url: str) -> bool:
    return urlsplit(url).path in ("", "/")


def document_extension(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(DOCUMENT_EXTENSIONS)


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether robots.txt lets the crawler fetch this URL. No robots.txt (4xx) allows; a site that can't be reached
    at all counts as allowed here — the page fetch that follows reports it."""
    parts = urlsplit(url)
    try:
        resp = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=PROBE_TIMEOUT_S)
    except httpx.HTTPError:
        return True
    if resp.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(CRAWLER_AGENT, url)


@dataclass
class Probe:
    content_type: str
    final_url: str
    title: str | None
    description: str | None


async def probe(client: httpx.AsyncClient, url: str) -> Probe:
    """HEAD, then GET if the server doesn't answer HEAD or it's a page (for its title), 8 s, redirects followed.
    Unreachable or an error status → 422."""
    try:
        resp = await client.head(url, timeout=PROBE_TIMEOUT_S)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        body = b""
        if resp.status_code >= 400 or ctype not in DOCUMENT_TYPES:
            async with client.stream("GET", url, timeout=PROBE_TIMEOUT_S) as resp:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code < 400 and ctype not in DOCUMENT_TYPES:
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) >= MAX_HEAD_BYTES:
                            break
    except httpx.HTTPError as e:
        raise ApiException(422, "validation_error", f"The site doesn't answer ({type(e).__name__})") from e
    if resp.status_code >= 400:
        raise ApiException(422, "validation_error", f"The site answered {resp.status_code} for this link")
    title = description = None
    if body:
        tree = HTMLParser(body.decode(resp.encoding or "utf-8", errors="replace"))
        node = tree.css_first("title")
        title = " ".join(node.text().split())[:200] if node else None
        meta = tree.css_first('meta[name="description"]') or tree.css_first('meta[property="og:description"]')
        description = (meta.attributes.get("content") or "").strip()[:500] if meta else None
    return Probe(ctype, str(resp.url), title or None, description or None)


def crawl_settings(url: str, kind: str) -> tuple[int | None, int | None]:
    """(depth, max pages): a site root like sites.toml's defaults; a deeper path a shallow crawl of that path."""
    if kind == "document":
        return None, None
    return (DEFAULTS["max_depth"], DEFAULTS["max_pages"]) if is_root(url) else (2, DEFAULTS["max_pages"])


# ─────────────── storage ───────────────


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
        registry = registry_counts(REGISTRY)
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
        return corpus_stats(self.pool).totals.model_dump()

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


def registry_fields(row: dict, registry: dict[str, dict]) -> dict:
    """Pages, documents and last crawl of a site source from registry.sqlite (when this machine has it)."""
    reg = registry.get(row["site_id"], {}) if row["kind"] == "site" else {}
    return {"pages": reg.get("pages", 0), "documents_found": reg.get("documents_found", 0),
            "documents_downloaded": reg.get("documents_downloaded", 0), "last_crawled": reg.get("last_crawled")}


def status_of(row: dict) -> str:
    """disabled > blocked > running > queued > failed (last job) > indexed (has chunks) > pending."""
    job = row.get("last_job") or {}
    if not row["enabled"]:
        return "disabled"
    if row["robots"] == "blocked":
        return "blocked"
    if job.get("status") in ("running", "queued"):
        return job["status"]
    if job.get("status") == "failed":
        return "failed"
    return "indexed" if row.get("chunks") else "pending"


def source_model(row: dict) -> SourceRow:
    job = row.get("last_job")
    active = job if job and job.get("status") in ("running", "queued") else None
    error = (job or {}).get("error") if (job or {}).get("status") == "failed" else None
    return SourceRow(
        id=row["id"], kind=row["kind"], url=row["url"], site_id=row["site_id"], title=row.get("title"),
        category=row.get("category"), category_source=row.get("category_source"),
        start_urls=list(row.get("start_urls") or []), max_depth=row.get("max_depth"), max_pages=row.get("max_pages"),
        enabled=row["enabled"], robots=row["robots"], status=status_of(row),
        pages=row.get("pages", 0), documents_found=row.get("documents_found", 0),
        documents_downloaded=row.get("documents_downloaded", 0), chunks=row.get("chunks", 0),
        lines=row.get("lines", 0), last_crawled=iso(row.get("last_crawled")),
        progress=SourceProgress(job_id=active["id"], stage=active.get("stage"), percent=active.get("percent") or 0,
                                eta_s=active.get("eta_s")) if active else None,
        last_error=error[:200] if error else None, created_at=iso(row["created_at"]),
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
    admin = store(request)
    rows = await run_in_threadpool(admin.list_sources)
    return SourceList(sources=[source_model(r) for r in rows],
                      totals=CorpusTotals(**await run_in_threadpool(admin.totals)))


def added(row: dict, detected: SourceDetected, merged_into: int | None = None) -> SourceAdded:
    return SourceAdded(**source_model(row).model_dump(), detected=detected, merged_into=merged_into)


@router.post("/sources", response_model=SourceAdded, status_code=201,
             responses={200: {"model": SourceAdded, "description": "merged into a source of the same domain"}})
async def add_source(req: SourceCreate, request: Request, response: Response) -> SourceAdded:
    """Paste a link: the server decides the rest. robots.txt first (nothing is fetched from a site that forbids it);
    then kind (PDF/DOC/DOCX by content type or extension, else a site), category by rules (no LLM), crawl settings;
    the job is queued at once. A second link of a domain that is already a source goes into that source."""
    url = normalize_url(req.url)
    site_id = site_of(url)
    client = http_client(request)
    admin = store(request)
    blocked = site_id in EXCLUDED_SITES or not await robots_allowed(client, url)
    title = text = None
    if blocked:
        kind = req.kind or ("document" if document_extension(url) else "site")
    else:
        found = await probe(client, url)
        kind = req.kind or ("document" if found.content_type in DOCUMENT_TYPES or document_extension(url) else "site")
        title, text = found.title, " ".join(x for x in (found.title, found.description) if x)

    existing = await run_in_threadpool(admin.find_by_site, site_id)
    if existing is not None:  # never a second row for a domain
        known = {existing["url"].rstrip("/"), *(u.rstrip("/") for u in existing.get("start_urls") or [])}
        if url.rstrip("/") in known or (kind == "site" and is_root(url)):
            raise ApiException(409, "conflict", f"Already a source: {existing['site_id']} (id {existing['id']})")
        row = await run_in_threadpool(admin.merge_url, existing["id"], url)
        depth, pages = crawl_settings(url, kind)
        if existing["robots"] == "blocked":
            reason = f"Saved into {site_id}, but its robots.txt forbids crawling: no crawl."
        elif not existing["enabled"]:
            reason = f"Saved into {site_id}, which is disabled: no crawl."
        elif req.start:
            await run_in_threadpool(admin.create_job, existing["id"], "crawl", url)
            row = await run_in_threadpool(admin.get_source, existing["id"])
            reason = (f"Added the document to {site_id}; downloading, parsing and indexing it." if kind == "document"
                      else f"Added the path {urlsplit(url).path} to {site_id}; crawling it up to depth {depth}.")
        else:
            reason = f"Added to {site_id}; no crawl started (start: false)."
        response.status_code = 200
        return added(row, SourceDetected(kind=kind, category=existing.get("category") or "other",
                                         category_source="existing", title=title, crawl_depth=depth, max_pages=pages,
                                         reason=reason), merged_into=existing["id"])

    category, category_source = (req.category, "manual") if req.category else categorize(site_id, text or "")
    depth, pages = crawl_settings(url, kind)
    depth, pages = req.max_depth if req.max_depth is not None else depth, req.max_pages or pages
    row = {"kind": kind, "url": url, "site_id": site_id, "title": title, "category": category,
           "category_source": category_source, "start_urls": [url] if kind == "site" else [], "max_depth": depth,
           "max_pages": pages, "robots": "blocked" if blocked else "allowed"}
    try:
        created = await run_in_threadpool(admin.add_source, row)
    except Duplicate as e:
        raise ApiException(409, "conflict", f"Already a source: {site_id}") from e
    how = {"rule": "by domain rule", "keywords": "by title keywords", "default": "no rule matched",
           "manual": "as sent"}[category_source]
    what = "a document" if kind == "document" else "a website"
    if blocked:
        reason = f"Detected {what} ({category}, {how}); robots.txt of {site_id} forbids crawling: saved, no crawl."
    elif req.start:
        job_url = None if is_root(url) or kind == "document" else url  # a deeper path: crawled under its prefix
        await run_in_threadpool(admin.create_job, created["id"], "crawl", job_url)
        created = await run_in_threadpool(admin.get_source, created["id"])
        reason = (f"Detected {what} ({category}, {how}); downloading, parsing and indexing it." if kind == "document"
                  else f"Detected {what} ({category}, {how}); crawling up to depth {depth}.")
    else:
        reason = f"Detected {what} ({category}, {how}); saved without a crawl (start: false)."
    return added(created, SourceDetected(kind=kind, category=category, category_source=category_source, title=title,
                                         crawl_depth=depth, max_pages=pages, reason=reason))


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


@router.post("/jobs/{job_id}/retry", response_model=Job, status_code=201)
async def retry_job(job_id: int, request: Request) -> Job:
    """Runs a finished job's work again as a new job."""
    admin = store(request)
    old = await run_in_threadpool(admin.get_job, job_id)
    if old is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    if old["status"] in ("queued", "running"):
        raise ApiException(409, "conflict", f"Job {job_id} is still {old['status']}")
    source = await run_in_threadpool(admin.get_source, old["source_id"]) if old["source_id"] is not None else None
    if source and source.get("last_job") and source["last_job"]["status"] in ("queued", "running"):
        raise ApiException(409, "conflict", f"Job {source['last_job']['id']} of this source is still running")
    return job_model(await run_in_threadpool(admin.retry_job, job_id))


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, request: Request) -> dict:
    deleted = await run_in_threadpool(store(request).delete_job, job_id)
    if deleted is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    if not deleted:
        raise ApiException(409, "conflict", f"Job {job_id} is queued or running: stop it first")
    return {"ok": True}


@router.delete("/jobs")
async def clear_jobs(request: Request) -> dict:
    """Removes the finished jobs from the history (queued and running ones stay)."""
    return {"deleted": await run_in_threadpool(store(request).clear_jobs)}


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
    return await run_in_threadpool(suggestions.add, req.question, req.lang, req.pinned,
                                   getattr(request.app.state, "translate", None))


@router.get("/suggestions", response_model=SuggestionList)
async def admin_suggestions(request: Request) -> SuggestionList:
    """Every quick question once, with its texts in every language and whether it is shown yet."""
    suggestions = getattr(request.app.state, "suggestions", None)
    if suggestions is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return SuggestionList(items=await run_in_threadpool(suggestions.admin_list))


@router.delete("/suggestions/{suggestion_id}")
async def hide_suggestion(suggestion_id: int, request: Request) -> dict:
    suggestions = getattr(request.app.state, "suggestions", None)
    if suggestions is None or not await run_in_threadpool(suggestions.hide, suggestion_id):
        raise ApiException(404, "not_found", f"No quick question {suggestion_id}")
    return {"ok": True}


# ─────────────── gaps: questions without a full answer ───────────────


def gaps_store(request: Request):
    gaps = getattr(request.app.state, "gaps", None)
    if gaps is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return gaps


@router.get("/gaps", response_model=GapList)
async def list_gaps(request: Request, status: str = "not_found,partial", lang: str | None = None,
                    days: int = Query(30, ge=1, le=3650), limit: int = Query(50, ge=1, le=500),
                    hidden: bool = False) -> GapList:
    """Similar not_found / partial questions grouped (local embeddings, no LLM), biggest groups first."""
    statuses = [x for x in status.split(",") if x in ("not_found", "partial")]
    if not statuses or lang not in (None, "ro", "ru"):
        raise ApiException(422, "validation_error", "status: not_found and/or partial; lang: ro or ru")
    return GapList(**await run_in_threadpool(gaps_store(request).list, statuses, lang, days, limit, hidden))


@router.post("/gaps/{gap_id}/recheck", response_model=GapRecheck)
async def recheck_gap(gap_id: str, request: Request) -> GapRecheck:
    """Asks the group's example again (one model call, only on this click). Answered now → the group leaves the
    default list."""
    result = await run_in_threadpool(gaps_store(request).recheck, gap_id, request.app.state.ask_once)
    if result is None:
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return GapRecheck(**result)


@router.post("/gaps/{gap_id}/hide")
async def hide_gap(gap_id: str, request: Request) -> dict:
    if not await run_in_threadpool(gaps_store(request).hide, gap_id, True):
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return {"ok": True}


@router.post("/gaps/{gap_id}/unhide")
async def unhide_gap(gap_id: str, request: Request) -> dict:
    if not await run_in_threadpool(gaps_store(request).hide, gap_id, False):
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return {"ok": True}
