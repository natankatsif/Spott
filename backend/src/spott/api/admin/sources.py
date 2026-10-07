"""Admin → Sources: the sources (a link pasted, the server decides the rest), their crawl jobs."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from spott.core.sources import categorize

from ..deps import service
from ..errors import ApiException
from ..schemas import (
    CorpusTotals,
    Job,
    JobCreate,
    JobList,
    SourceAdded,
    SourceCreate,
    SourceDetected,
    SourceList,
    SourcePatch,
    SourceRow,
)
from .auth import require_admin
from .probe import Link, crawl_settings, inspect, is_root, normalize_url
from .store import AdminStore, Duplicate
from .views import job_model, source_model

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def admin_store(request: Request) -> AdminStore:
    return service(request, "admin")


@router.get("/sources", response_model=SourceList)
async def list_sources(request: Request) -> SourceList:
    admin = admin_store(request)
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
    client, admin = service(request, "http"), admin_store(request)
    link = await inspect(client, url, req.kind)
    existing = await run_in_threadpool(admin.find_by_site, link.site_id)
    if existing is not None:  # never a second row for a domain
        response.status_code = 200
        return await merge_into(admin, existing, link, req.start)
    return await create(admin, link, req)


async def merge_into(admin: AdminStore, existing: dict, link: Link, start: bool) -> SourceAdded:
    """A deeper path or a document of a domain that is already a source: into its start URLs, crawled on its own."""
    url, site_id, kind = link.url, link.site_id, link.kind
    known = {existing["url"].rstrip("/"), *(u.rstrip("/") for u in existing.get("start_urls") or [])}
    if url.rstrip("/") in known or (kind == "site" and is_root(url)):
        raise ApiException(409, "conflict", f"Already a source: {existing['site_id']} (id {existing['id']})")
    row = await run_in_threadpool(admin.merge_url, existing["id"], url)
    depth, pages = crawl_settings(url, kind)
    if existing["robots"] == "blocked":
        reason = f"Saved into {site_id}, but its robots.txt forbids crawling: no crawl."
    elif not existing["enabled"]:
        reason = f"Saved into {site_id}, which is disabled: no crawl."
    elif start:
        await run_in_threadpool(admin.create_job, existing["id"], "crawl", url)
        row = await run_in_threadpool(admin.get_source, existing["id"])
        reason = (f"Added the document to {site_id}; downloading, parsing and indexing it." if kind == "document"
                  else f"Added the path {urlsplit(url).path} to {site_id}; crawling it up to depth {depth}.")
    else:
        reason = f"Added to {site_id}; no crawl started (start: false)."
    return added(row, SourceDetected(kind=kind, category=existing.get("category") or "other",
                                     category_source="existing", title=link.title, crawl_depth=depth, max_pages=pages,
                                     reason=reason), merged_into=existing["id"])


async def create(admin: AdminStore, link: Link, req: SourceCreate) -> SourceAdded:
    """A new source: its category by rules (or as sent), crawl settings by the kind of link, the crawl queued."""
    url, site_id, kind = link.url, link.site_id, link.kind
    category, category_source = (req.category, "manual") if req.category else categorize(site_id, link.text)
    depth, pages = crawl_settings(url, kind)
    depth, pages = req.max_depth if req.max_depth is not None else depth, req.max_pages or pages
    row = {"kind": kind, "url": url, "site_id": site_id, "title": link.title, "category": category,
           "category_source": category_source, "start_urls": [url] if kind == "site" else [], "max_depth": depth,
           "max_pages": pages, "robots": "blocked" if link.blocked else "allowed"}
    try:
        created = await run_in_threadpool(admin.add_source, row)
    except Duplicate as e:
        raise ApiException(409, "conflict", f"Already a source: {site_id}") from e
    how = {"rule": "by domain rule", "keywords": "by title keywords", "default": "no rule matched",
           "manual": "as sent"}[category_source]
    what = "a document" if kind == "document" else "a website"
    if link.blocked:
        reason = f"Detected {what} ({category}, {how}); robots.txt of {site_id} forbids crawling: saved, no crawl."
    elif req.start:
        job_url = None if is_root(url) or kind == "document" else url  # a deeper path: crawled under its prefix
        await run_in_threadpool(admin.create_job, created["id"], "crawl", job_url)
        created = await run_in_threadpool(admin.get_source, created["id"])
        reason = (f"Detected {what} ({category}, {how}); downloading, parsing and indexing it." if kind == "document"
                  else f"Detected {what} ({category}, {how}); crawling up to depth {depth}.")
    else:
        reason = f"Detected {what} ({category}, {how}); saved without a crawl (start: false)."
    return added(created, SourceDetected(kind=kind, category=category, category_source=category_source,
                                         title=link.title, crawl_depth=depth, max_pages=pages, reason=reason))


@router.patch("/sources/{source_id}", response_model=SourceRow)
async def patch_source(source_id: int, req: SourcePatch, request: Request) -> SourceRow:
    row = await run_in_threadpool(admin_store(request).patch_source, source_id, req.model_dump(exclude_none=True))
    if row is None:
        raise ApiException(404, "not_found", f"No source {source_id}")
    return source_model(row)


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, request: Request, purge: bool = False) -> dict:
    if not await run_in_threadpool(admin_store(request).delete_source, source_id, purge):
        raise ApiException(404, "not_found", f"No source {source_id}")
    return {"ok": True}


@router.post("/sources/{source_id}/jobs", response_model=Job, status_code=201)
async def start_job(source_id: int, req: JobCreate, request: Request) -> Job:
    admin = admin_store(request)
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
    return JobList(jobs=[job_model(j) for j in await run_in_threadpool(admin_store(request).list_jobs, status)])


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: int, request: Request) -> Job:
    job = await run_in_threadpool(admin_store(request).get_job, job_id)
    if job is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    return job_model(job)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: int, request: Request) -> Job:
    job = await run_in_threadpool(admin_store(request).cancel_job, job_id)
    if job is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    return job_model(job)


@router.post("/jobs/{job_id}/retry", response_model=Job, status_code=201)
async def retry_job(job_id: int, request: Request) -> Job:
    """Runs a finished job's work again as a new job."""
    admin = admin_store(request)
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
    deleted = await run_in_threadpool(admin_store(request).delete_job, job_id)
    if deleted is None:
        raise ApiException(404, "not_found", f"No job {job_id}")
    if not deleted:
        raise ApiException(409, "conflict", f"Job {job_id} is queued or running: stop it first")
    return {"ok": True}


@router.delete("/jobs")
async def clear_jobs(request: Request) -> dict:
    """Removes the finished jobs from the history (queued and running ones stay)."""
    return {"deleted": await run_in_threadpool(admin_store(request).clear_jobs)}
