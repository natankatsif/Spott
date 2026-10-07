"""The registry shared by all ingest stages: Postgres tables registry_* (schema: spott.core.db.REGISTRY_SQL).

    registry_pages              HTML pages fetched by the crawler
    registry_documents          one row per document URL; status of its download
    registry_document_sources   every page where a document link was found (provenance for citations)
    registry_files              unique downloaded contents, keyed by SHA-256; status of parsing

New content at a known document URL is its next version: registry_documents counts the versions, and the index
replaces the old one.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from spott.core.db import get_connection, init_registry_db
from spott.ingest.common.urls import url_key


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    """A registry timestamp as parsed files and the index carry it: ISO 8601 in UTC, to the second."""
    return value.astimezone(UTC).isoformat(timespec="seconds") if value else None


class Registry:
    def __init__(self, conn: psycopg.Connection):
        """On an autocommit connection to a database with the registry tables (Registry.open() sees to both)."""
        self.conn = conn

    @classmethod
    def open(cls) -> "Registry":
        """The registry in the configured database, its tables created if they are not there yet."""
        conn = get_connection(autocommit=True, register=False)
        try:
            init_registry_db(conn)
        except BaseException:
            conn.close()
            raise
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def _rows(self, sql: str, params: Sequence | None = None) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(sql, params).fetchall()

    def _write(self, sql: str, params: Sequence | None = None) -> int:
        """Runs an INSERT / UPDATE; returns how many rows it changed."""
        return self.conn.execute(sql, params).rowcount

    # --- crawler --------------------------------------------------------------

    def upsert_page(self, page: dict) -> None:
        """A fetched page. Fetched again, it is a new copy: parsed again, its parse fields start over."""
        columns = ("url", "site", "status", "depth", "title", "lang", "html_file", "error", "fetched_at")
        self._write(
            f"INSERT INTO registry_pages ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT (url) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in columns[1:])}, "
            "parse_status = DEFAULT, parsed_at = NULL, parse_error = NULL",
            [page.get(c) for c in columns])

    def page_fetch_failed(self, page: dict) -> None:
        """A page that couldn't be fetched this time (network error, 5xx). One that was fetched before keeps its
        last good copy, and so its place in the index, with the error noted; a new one is recorded as failed."""
        kept = self._write(
            "UPDATE registry_pages SET error = %s WHERE url = %s AND status < 400 AND html_file IS NOT NULL",
            (page.get("error") or f"HTTP {page.get('status')}", page["url"]))
        if not kept:
            self.upsert_page(page)

    def drop_unseen_pages(self, site: str, since: datetime) -> int:
        """After a complete crawl of a whole site: its pages not reached this time (no longer linked or gone) stop
        being current, so indexing removes them. Returns how many."""
        return self._write(
            "UPDATE registry_pages SET status = 410, error = 'not reached by the last complete crawl' "
            "WHERE site = %s AND fetched_at < %s AND (status IS NULL OR status < 400)",
            (site, since))

    def add_document(self, *, key: str, url: str, site: str, extension: str, source: dict) -> bool:
        """Registers a document link and where it was found ({found_on, anchor_text, depth}). Returns True if the
        document is new."""
        ts = now()
        with self.conn.transaction():
            new = self._write(
                "INSERT INTO registry_documents (key, url, site, extension, discovered_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING",
                (key, url, site, extension, ts)) == 1
            self._write(
                "INSERT INTO registry_document_sources (document_key, found_on, anchor_text, depth, discovered_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (key, source.get("found_on") or "", source.get("anchor_text"), source.get("depth"), ts))
        return new

    def record_crawl_missing(self, site: str, seen_keys: set[str]) -> tuple[int, int]:
        """Called after a complete crawl of a site. Every document of the site it didn't see is missing once more;
        missing on 2 crawls in a row, it becomes 'removed'. Returns (num_missing, num_removed)."""
        seen = list(seen_keys)
        with self.conn.transaction():
            self._write("UPDATE registry_documents SET consecutive_missing = 0 WHERE site = %s AND key = ANY(%s)",
                        (site, seen))
            removed = [r["removed"] for r in self._rows(
                "UPDATE registry_documents SET consecutive_missing = consecutive_missing + 1, "
                "status = CASE WHEN consecutive_missing + 1 >= 2 THEN 'removed' ELSE status END, "
                "removed_at = CASE WHEN consecutive_missing + 1 >= 2 THEN COALESCE(removed_at, %s) "
                "ELSE removed_at END "
                "WHERE site = %s AND NOT key = ANY(%s) RETURNING consecutive_missing >= 2 AS removed",
                (now(), site, seen))]
        return removed.count(False), removed.count(True)

    # --- downloader -----------------------------------------------------------

    def documents_to_download(self, statuses: list[str], sites: list[str] | None, limit: int | None) -> list[dict]:
        query = "SELECT * FROM registry_documents WHERE status = ANY(%s) AND status != 'removed'"
        params: list = [list(statuses)]
        if sites:
            query += " AND site = ANY(%s)"
            params.append(list(sites))
        query += " ORDER BY discovered_at, key"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        return self._rows(query, params)

    def documents_by_keys(self, keys: list[str]) -> list[dict]:
        return self._rows("SELECT * FROM registry_documents WHERE key = ANY(%s)", (list(keys),)) if keys else []

    def has_file(self, sha256: str) -> bool:
        return bool(self._rows("SELECT 1 FROM registry_files WHERE sha256 = %s", (sha256,)))

    def record_download(self, key: str, *, sha256: str, path: str | None, extension: str, http_status: int,
                        etag: str | None, last_modified: str | None) -> None:
        """Stores a successful download. New content at a known URL is the document's next version."""
        ts = now()
        with self.conn.transaction():
            if path is not None:
                self._write(
                    "INSERT INTO registry_files (sha256, path, extension, downloaded_at) VALUES (%s, %s, %s, %s)",
                    (sha256, path, extension, ts))
            rows = self._rows("SELECT sha256, version FROM registry_documents WHERE key = %s", (key,))
            row = rows[0] if rows else None
            old_sha = row["sha256"] if row else None
            version = row["version"] if row else 1
            changed = bool(old_sha and old_sha != sha256)
            if changed:
                version += 1
            if changed:
                self._write(
                    "UPDATE registry_documents SET status = 'downloaded', sha256 = %s, version = %s, updated_at = %s, "
                    "etag = %s, last_modified = %s, "
                    "http_status = %s, error = NULL, checked_at = %s, consecutive_missing = 0, removed_at = NULL "
                    "WHERE key = %s",
                    (sha256, version, ts, etag, last_modified, http_status, ts, key))
            else:
                self._write(
                    "UPDATE registry_documents SET status = 'downloaded', sha256 = %s, etag = %s, last_modified = %s, "
                    "http_status = %s, error = NULL, checked_at = %s, consecutive_missing = 0, removed_at = NULL "
                    "WHERE key = %s",
                    (sha256, etag, last_modified, http_status, ts, key))

    def record_download_missing(self, key: str, http_status: int) -> bool:
        """Called when downloader receives 404/410. Increments consecutive_missing.
        If >= 2, status becomes 'removed'. Returns True if marked removed. A document that has a downloaded version
        keeps it (and stays in the index) after the first miss: one 404 can be a hiccup of the site."""
        ts = now()
        rows = self._rows(
            "UPDATE registry_documents SET consecutive_missing = consecutive_missing + 1, http_status = %s, "
            "checked_at = %s, "
            "status = CASE WHEN consecutive_missing + 1 >= 2 THEN 'removed' "
            "              WHEN status = 'downloaded' THEN status ELSE 'missing' END, "
            "removed_at = CASE WHEN consecutive_missing + 1 >= 2 THEN COALESCE(removed_at, %s) ELSE removed_at END "
            "WHERE key = %s RETURNING consecutive_missing >= 2 AS removed",
            (http_status, ts, ts, key))
        return bool(rows and rows[0]["removed"])

    def mark_checked(self, key: str, status: str, *, http_status: int | None = None, error: str | None = None) -> None:
        """A download attempt that got no content. A failure (timeout, 5xx) of a document that has a downloaded
        version keeps it 'downloaded': a site's bad moment must not take a good document out of the index."""
        self._write(
            "UPDATE registry_documents SET "
            "status = CASE WHEN %s = 'failed' AND status = 'downloaded' THEN status ELSE %s END, "
            "http_status = %s, error = %s, checked_at = %s WHERE key = %s",
            (status, status, http_status, error, now(), key))

    def mark_not_modified(self, key: str) -> None:
        self._write(
            "UPDATE registry_documents SET http_status = 304, checked_at = %s, consecutive_missing = 0 WHERE key = %s",
            (now(), key))

    # --- parsing --------------------------------------------------------------

    def files_to_parse(self, statuses: list[str], limit: int | None, sha_prefixes: list[str] | None = None,
                       sites: list[str] | None = None) -> list[dict]:
        query = "SELECT * FROM registry_files WHERE parse_status = ANY(%s)"
        params: list = [list(statuses)]
        if sites:  # only files some document of these sites points to (a job for one site parses that site)
            query += " AND sha256 IN (SELECT sha256 FROM registry_documents WHERE site = ANY(%s))"
            params.append(list(sites))
        if sha_prefixes:
            query += " AND sha256 LIKE ANY(%s)"
            params.append([f"{p}%" for p in sha_prefixes])
        query += " ORDER BY downloaded_at, sha256"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        return self._rows(query, params)

    def files_of_documents(self, keys: list[str], statuses: list[str]) -> list[dict]:
        """The files (by status) that these documents currently point to."""
        if not keys:
            return []
        return self._rows(
            "SELECT * FROM registry_files WHERE parse_status = ANY(%s) "
            "AND sha256 IN (SELECT sha256 FROM registry_documents WHERE key = ANY(%s)) "
            "ORDER BY downloaded_at, sha256",
            (list(statuses), list(keys)))

    def file_sources(self, sha256: str) -> list[dict]:
        """Every document URL with this content and every page it was found on."""
        return self._sources([sha256]).get(sha256, [])

    def _sources(self, shas: list[str]) -> dict[str, list[dict]]:
        """file_sources() of many files in one query: nearest pages first."""
        by_sha: dict[str, list[dict]] = {}
        for row in self._rows(
                "SELECT d.sha256, d.url, d.site, s.found_on, s.anchor_text "
                "FROM registry_documents d JOIN registry_document_sources s ON s.document_key = d.key "
                "WHERE d.sha256 = ANY(%s) ORDER BY s.depth NULLS LAST, s.discovered_at, d.key, s.found_on",
                (shas,)):
            sha = row.pop("sha256")
            by_sha.setdefault(sha, []).append({k: v for k, v in row.items() if v not in (None, "")})
        return by_sha

    def mark_parsed(self, sha256: str, status: str, *, error: str | None = None) -> None:
        self._write(
            "UPDATE registry_files SET parse_status = %s, parse_error = %s, parsed_at = %s WHERE sha256 = %s",
            (status, error, now(), sha256))

    def fail_interrupted_parses(self) -> int:
        """Files left 'parsing' by a run that died on them — the OOM killer does not let the run write anything.
        They become 'failed', so the next run moves past them instead of dying on the same file again and again;
        --retry-failed gives them another chance (on a bigger machine, say)."""
        return self._write(
            "UPDATE registry_files SET parse_status = 'failed', parsed_at = %s, "
            "parse_error = 'Killed while parsing (out of memory?): skipped so the next run does not die on it' "
            "WHERE parse_status = 'parsing'",
            (now(),))

    # --- pages parsing --------------------------------------------------------

    def pages_to_parse(self, sites: list[str] | None = None, limit: int | None = None,
                       reparse: bool = False) -> list[dict]:
        query = "SELECT * FROM registry_pages WHERE status < 400 AND html_file IS NOT NULL"
        params: list = []
        if not reparse:
            query += " AND parse_status = 'pending'"
        if sites:
            query += " AND site = ANY(%s)"
            params.append(list(sites))
        query += " ORDER BY fetched_at, url"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        return self._rows(query, params)

    def site_page_keys(self, site: str) -> set[str]:
        return {url_key(r["url"]) for r in self._rows("SELECT url FROM registry_pages WHERE site = %s", (site,))}

    def site_pages(self, site: str) -> list[dict]:
        """Every crawled page of a site with its HTML on disk (context for boilerplate detection)."""
        return self._rows("SELECT * FROM registry_pages WHERE site = %s AND status < 400 AND html_file IS NOT NULL "
                          "ORDER BY url", (site,))

    def mark_page_parsed(self, url: str, status: str, *, error: str | None = None) -> None:
        self._write("UPDATE registry_pages SET parse_status = %s, parsed_at = %s, parse_error = %s WHERE url = %s",
                    (status, now(), error, url))

    def status_counts(self) -> dict[str, dict[str, int]]:
        def counts(column: str, table: str) -> dict[str, int]:
            return {r["status"]: r["n"] for r in self._rows(
                f"SELECT {column} AS status, COUNT(*) AS n FROM {table} GROUP BY {column} ORDER BY {column}")}

        return {"documents": counts("status", "registry_documents"),
                "files": counts("parse_status", "registry_files"),
                "pages": counts("parse_status", "registry_pages")}

    def active_file_documents(self) -> list[dict]:
        """Returns active (non-removed) downloaded & parsed documents.
        Deduplicates identical file content (sha256): primary URL is the earliest discovered,
        all other URLs are added to sources."""
        rows = self._rows(
            """
            SELECT d.key, d.url, d.sha256, d.updated_at
            FROM registry_documents d
            JOIN registry_files f ON d.sha256 = f.sha256
            WHERE d.status = 'downloaded' AND f.parse_status = 'parsed'
            ORDER BY d.sha256, d.discovered_at, d.key
            """)

        by_sha: dict[str, list[dict]] = {}
        for r in rows:
            by_sha.setdefault(r["sha256"], []).append(r)
        sources = self._sources(list(by_sha))

        result = []
        for sha, doc_rows in by_sha.items():
            primary = doc_rows[0]
            primary_key = primary["key"] or url_key(primary["url"])
            result.append({
                "doc_id": f"file:{primary_key}",
                "primary_url": primary["url"],
                "url_key": primary_key,
                "sha256": sha,
                "updated_at": iso(primary["updated_at"]),
                "sources": sources.get(sha, []),
            })
        return result

    def active_page_documents(self) -> list[dict]:
        """Returns all parsed active (non-removed) pages."""
        rows = self._rows(
            """
            SELECT url, fetched_at
            FROM registry_pages
            WHERE status < 400 AND parse_status = 'parsed' AND html_file IS NOT NULL
            ORDER BY fetched_at, url
            """)
        result = []
        for r in rows:
            ukey = url_key(r["url"])
            result.append({
                "doc_id": f"page:{ukey}",
                "url": r["url"],
                "url_key": ukey,
                "fetched_at": iso(r["fetched_at"]),
            })
        return result
