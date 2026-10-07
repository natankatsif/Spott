"""What the admin's sources page counts: per site, what the registry crawled and the autopilot still has to do;
the corpus totals of the page header."""

import json
from datetime import UTC
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

from spott.core.paths import DATA_DIR

from .schemas import CorpusTotals

CRAWL_DIR = DATA_DIR / "crawl"


def registry_counts(conn: psycopg.Connection, crawl_dir: Path = CRAWL_DIR) -> dict[str, dict]:
    """Per site: pages, documents found / downloaded / replaced / removed, last crawl. {} before anything is crawled."""
    per_site: dict[str, dict] = {}
    try:
        for site, pages, last in conn.execute(
                "SELECT site, COUNT(*), MAX(fetched_at) FROM registry_pages GROUP BY site").fetchall():
            per_site[site] = {"pages": pages, "last_crawled": last.astimezone(UTC).isoformat(timespec="seconds")}
        for site, found, downloaded, replaced, removed in conn.execute(
                "SELECT site, COUNT(*), COUNT(*) FILTER (WHERE status = 'downloaded'), "
                "COUNT(*) FILTER (WHERE version > 1), COUNT(*) FILTER (WHERE status = 'removed') "
                "FROM registry_documents GROUP BY site").fetchall():
            per_site.setdefault(site, {}).update(
                documents_found=found, documents_downloaded=downloaded,
                documents_replaced=replaced, documents_removed=removed)
        add_work_left(conn, per_site, crawl_dir)
    except psycopg.errors.UndefinedTable:
        return {}
    return per_site


def add_work_left(conn: psycopg.Connection, per_site: dict[str, dict], crawl_dir: Path) -> None:
    """What the autopilot still has to do per site (worker/schedule.py: next_backlog): documents found but never
    fetched, downloaded files and crawled pages not parsed yet, and pages the last crawl did not reach."""
    for site, undownloaded, files in conn.execute(
        "SELECT d.site, COUNT(*) FILTER (WHERE d.status = 'discovered'), "
        "       COUNT(DISTINCT f.sha256) FILTER (WHERE f.parse_status = 'pending') "
        "FROM registry_documents d LEFT JOIN registry_files f ON f.sha256 = d.sha256 "
        "WHERE d.status IN ('discovered', 'downloaded') GROUP BY d.site"
    ).fetchall():
        per_site.setdefault(site, {}).update(documents_pending=undownloaded, files_pending=files)
    for site, pages in conn.execute("SELECT site, COUNT(*) FROM registry_pages WHERE parse_status = 'pending' "
                                    "AND status < 400 AND html_file IS NOT NULL GROUP BY site").fetchall():
        per_site.setdefault(site, {})["pages_pending"] = pages
    for site in list(per_site):
        try:
            state = json.loads((crawl_dir / site / "state.json").read_text(encoding="utf-8"))
            per_site[site]["crawl_left"] = len(state.get("queue") or [])
        except (OSError, ValueError):
            pass


def corpus_totals(pool: ConnectionPool) -> CorpusTotals:
    """Over every site source: what the registry crawled (a site this database's registry knows nothing of, an index
    restored from a dump, counts what the index holds) and what is indexed."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT site_id FROM sources WHERE kind = 'site'")
        sites = [site for (site,) in cur.fetchall()]
        cur.execute("SELECT site, COUNT(*) FROM chunks GROUP BY site")
        chunks = dict(cur.fetchall())
        cur.execute("SELECT site, COUNT(*) FILTER (WHERE kind = 'page'), COUNT(*) FILTER (WHERE kind = 'file') "
                    "FROM documents GROUP BY site")
        indexed = {site: {"pages": pages, "documents_found": files, "documents_downloaded": files}
                   for site, pages, files in cur.fetchall()}
        cur.execute("SELECT COUNT(*) FROM lines")
        [lines] = cur.fetchone()
    with pool.connection() as conn:
        registry = registry_counts(conn)

    def total(key: str) -> int:
        return sum(registry.get(site, {}).get(key, indexed.get(site, {}).get(key, 0)) for site in sites)

    return CorpusTotals(
        sites_total=len(sites),
        sites_indexed=sum(1 for site in sites if chunks.get(site)),
        pages=total("pages"),
        documents_found=total("documents_found"),
        documents_downloaded=total("documents_downloaded"),
        chunks=sum(chunks.get(site, 0) for site in sites),
        lines=lines,
        documents_replaced=sum(r.get("documents_replaced", 0) for r in registry.values()),
        documents_removed=sum(r.get("documents_removed", 0) for r in registry.values()),
    )
