"""Corpus stats, every Annex-1 site, what is crawled and indexed: the totals of the admin's sources page.

Sources: the admin panel's `sources` table (all sites, robots status; sites.toml only until it is imported),
the registry (pages and documents per site, where this database was crawled into), the index (chunks, lines).
"""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

from .files import DATA_DIR
from .schemas import CorpusStats, CorpusTotals, SiteStats

SITES_TOML = DATA_DIR / "sources" / "sites.toml"
CRAWL_DIR = DATA_DIR / "crawl"
BLOCKED = {"chisinau.md", "actelocale.gov.md"}  # robots.txt: Disallow: /


def load_sites(path: Path = SITES_TOML) -> list[dict]:
    if not path.is_file():
        return []
    return [s | {"blocked": s["id"] in BLOCKED}
            for s in tomllib.loads(path.read_text(encoding="utf-8")).get("site", [])]


def source_sites(pool: ConnectionPool) -> list[dict]:
    """Site sources from the admin panel; [] before `python -m spott.ingest.tools.sources import` has filled the table."""
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT site_id, category, robots FROM sources WHERE kind = 'site' ORDER BY id")
            return [{"id": sid, "category": cat, "blocked": robots == "blocked"} for sid, cat, robots in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        return []


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


def index_counts(pool: ConnectionPool) -> tuple[dict[str, dict], int, str | None]:
    """Per site: chunks and indexed documents; total lines; last indexing time."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT site, COUNT(*) FROM chunks GROUP BY site")
        chunks = {site: n for site, n in cur.fetchall()}
        cur.execute("SELECT site, COUNT(*) FILTER (WHERE kind = 'page'), COUNT(*) FILTER (WHERE kind = 'file') "
                    "FROM documents GROUP BY site")
        docs = {site: (pages, files) for site, pages, files in cur.fetchall()}
        cur.execute("SELECT COUNT(*) FROM lines")
        lines = cur.fetchone()[0]
        cur.execute("SELECT MAX(indexed_at) FROM documents")
        indexed_at = cur.fetchone()[0]
    per_site = {site: {"chunks": chunks.get(site, 0), "indexed_pages": docs.get(site, (0, 0))[0],
                       "indexed_files": docs.get(site, (0, 0))[1]} for site in set(chunks) | set(docs)}
    return per_site, lines, indexed_at.isoformat(timespec="seconds") if indexed_at else None


def corpus_stats(pool: ConnectionPool, sites_path: Path = SITES_TOML) -> CorpusStats:
    sites = source_sites(pool) or load_sites(sites_path)
    with pool.connection() as conn:
        registry = registry_counts(conn)
    index, lines, indexed_at = index_counts(pool)

    rows = []
    for site in sites:
        sid = site["id"]
        reg, idx = registry.get(sid, {}), index.get(sid, {})
        chunks = idx.get("chunks", 0)
        rows.append(SiteStats(
            site=sid,
            category=site.get("category"),
            status="indexed" if chunks else "blocked" if site["blocked"] else "pending",
            # A site this database's registry knows nothing of (an index restored from a dump): what the index holds.
            pages=reg.get("pages", idx.get("indexed_pages", 0)),
            documents_found=reg.get("documents_found", idx.get("indexed_files", 0)),
            documents_downloaded=reg.get("documents_downloaded", idx.get("indexed_files", 0)),
            chunks=chunks,
            last_crawled=reg.get("last_crawled"),
        ))
    totals = CorpusTotals(
        sites_total=len(rows),
        sites_indexed=sum(r.status == "indexed" for r in rows),
        pages=sum(r.pages for r in rows),
        documents_found=sum(r.documents_found for r in rows),
        documents_downloaded=sum(r.documents_downloaded for r in rows),
        chunks=sum(r.chunks for r in rows),
        lines=lines,
        documents_replaced=sum(v.get("documents_replaced", 0) for v in registry.values()),
        documents_removed=sum(v.get("documents_removed", 0) for v in registry.values()),
    )
    updated = indexed_at or datetime.now(UTC).isoformat(timespec="seconds")
    return CorpusStats(updated_at=updated, totals=totals, sites=rows)
