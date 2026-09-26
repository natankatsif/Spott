"""Corpus health dashboard (GET /api/corpus/stats): every Annex-1 site, what is crawled and indexed.

Sources: the admin panel's `sources` table (all sites, robots status; sites.toml only until it is imported),
registry.sqlite (pages and documents per site, if present on this machine), Postgres (chunks per site, lines).
"""

import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

from .files import DATA_DIR
from .schemas import CorpusStats, CorpusTotals, SiteStats

SITES_TOML = DATA_DIR / "sources" / "sites.toml"
REGISTRY = DATA_DIR / "registry.sqlite"
BLOCKED = {"chisinau.md", "actelocale.gov.md"}  # robots.txt: Disallow: /


def load_sites(path: Path = SITES_TOML) -> list[dict]:
    if not path.is_file():
        return []
    return [s | {"blocked": s["id"] in BLOCKED}
            for s in tomllib.loads(path.read_text(encoding="utf-8")).get("site", [])]


def source_sites(pool: ConnectionPool) -> list[dict]:
    """Site sources from the admin panel; [] before `python -m tools.sources import` has filled the table."""
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT site_id, category, robots FROM sources WHERE kind = 'site' ORDER BY id")
            return [{"id": sid, "category": cat, "blocked": robots == "blocked"} for sid, cat, robots in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        return []


def registry_counts(path: Path = REGISTRY) -> dict[str, dict]:
    """Per site: pages, documents found / downloaded / replaced / removed, last crawl. {} without a registry."""
    if not path.is_file():
        return {}
    per_site: dict[str, dict] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for site, pages, last in conn.execute("SELECT site, COUNT(*), MAX(fetched_at) FROM pages GROUP BY site"):
            per_site[site] = {"pages": pages, "last_crawled": last}
        doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        replaced = "SUM(version > 1)" if "version" in doc_columns else "0"
        for site, found, downloaded, repl, removed in conn.execute(
            f"SELECT site, COUNT(*), SUM(status = 'downloaded'), {replaced}, SUM(status = 'removed') "
            "FROM documents GROUP BY site"
        ):
            per_site.setdefault(site, {}).update(
                documents_found=found, documents_downloaded=downloaded or 0,
                documents_replaced=repl or 0, documents_removed=removed or 0)
    finally:
        conn.close()
    return per_site


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


def corpus_stats(pool: ConnectionPool, sites_path: Path = SITES_TOML, registry_path: Path = REGISTRY) -> CorpusStats:
    sites = source_sites(pool) or load_sites(sites_path)
    registry = registry_counts(registry_path)
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
            # Without the registry on this machine, fall back to what the index holds.
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
