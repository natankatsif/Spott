"""Rows of the admin's store → the API's models: a source with its status and progress, a job."""

import json
import re
from datetime import datetime
from typing import Any

from ..schemas import Job, SourceProgress, SourceRow


def iso(value: Any) -> str | None:
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else value


def job_model(row: dict) -> Job:
    stats = row.get("stats") or {}
    return Job(**{k: row.get(k) for k in Job.model_fields if k not in ("started_at", "finished_at", "stats",
                                                                         "log_tail")},
               started_at=iso(row.get("started_at")), finished_at=iso(row.get("finished_at")),
               stats=json.loads(stats) if isinstance(stats, str) else stats,
               log_tail=list(row.get("log_tail") or []))


# What a stage prints per item: "acc.md [12/340] downloaded https://acc.md/f/x.pdf" (downloader),
# "[12/200] parsed (ocr) raw/ab/cd.pdf 3.2s" (parsing).
ITEM_LINE = re.compile(r"\[\d+/\d+]\s+(?P<outcome>\S+)(?:\s+\(ocr\))?\s+(?P<target>\S+)")


def current_item(log_tail: list[str]) -> str | None:
    """The file or address the running stage is on, for the admin to see what is happening right now. Taken from
    the last per-item line the stage printed; the crawler and the indexer report counts, not items, so they have
    none. Only the name is shown — a full path or URL would not fit the table."""
    for line in reversed(log_tail or []):
        if m := ITEM_LINE.search(line):
            target = m["target"].rstrip("/")
            name = target.rsplit("/", 1)[-1] or target
            return f"{m['outcome']} {name}"[:120]
    return None


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
                                eta_s=active.get("eta_s"),
                                current=current_item(active.get("log_tail") or [])) if active else None,
        last_error=error[:200] if error else None, created_at=iso(row["created_at"]),
        last_job=job_model(job) if job else None,
        auto_update=row.get("auto_update", True), check_method=row.get("check_method"),
        last_checked_at=iso(row.get("last_checked_at")), next_check_at=iso(row.get("next_check_at")),
        stale_signals=row.get("stale_signals") or 0,
        **{k: row.get(k) or 0 for k in ("crawl_left", "documents_pending", "files_pending", "pages_pending")})
