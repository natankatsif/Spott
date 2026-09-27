"""SQLite registry shared by all offline indexation stages (data/registry.sqlite).

    pages              HTML pages fetched by the crawler
    documents          one row per document URL; status of its download
    document_sources   every page where a document link was found (provenance for citations)
    files              unique downloaded contents, keyed by SHA-256; status of parsing
    document_versions  history of contents seen at each document URL
"""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url           TEXT PRIMARY KEY,          -- final URL after redirects
    site          TEXT NOT NULL,
    status        INTEGER,
    depth         INTEGER,
    parent        TEXT,
    anchor_text   TEXT,
    title         TEXT,
    lang          TEXT,
    alternates    TEXT,                      -- JSON {hreflang: url}
    html_file     TEXT,                      -- relative to data/crawl/<site>/
    content_type  TEXT,
    error         TEXT,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_site ON pages(site);

CREATE TABLE IF NOT EXISTS documents (
    key            TEXT PRIMARY KEY,         -- url_key(): ignores scheme, www., trailing slash
    url            TEXT NOT NULL,
    site           TEXT NOT NULL,            -- site where first discovered
    category       TEXT,
    extension      TEXT,
    external       INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'discovered',  -- discovered | downloaded | not_a_file | failed | removed | missing
    sha256         TEXT REFERENCES files(sha256),       -- current content
    etag           TEXT,
    last_modified  TEXT,
    http_status    INTEGER,
    error          TEXT,
    discovered_at  TEXT NOT NULL,
    checked_at     TEXT,                     -- last download attempt
    version        INTEGER NOT NULL DEFAULT 1,
    previous_sha256 TEXT,
    updated_at     TEXT,
    consecutive_missing INTEGER NOT NULL DEFAULT 0,
    removed_at     TEXT
);
CREATE INDEX IF NOT EXISTS documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS documents_sha256 ON documents(sha256);

CREATE TABLE IF NOT EXISTS document_sources (
    document_key   TEXT NOT NULL REFERENCES documents(key),
    found_on       TEXT NOT NULL DEFAULT '', -- page URL; '' when unknown
    found_on_title TEXT,
    anchor_text    TEXT,
    site           TEXT NOT NULL,
    depth          INTEGER,
    via            TEXT,                     -- a | iframe | embed | object | content-type | wp-media
    published      TEXT,                     -- upload date from WordPress media
    discovered_at  TEXT NOT NULL,
    PRIMARY KEY (document_key, found_on)
);

CREATE TABLE IF NOT EXISTS files (
    sha256          TEXT PRIMARY KEY,
    path            TEXT NOT NULL,           -- relative to data/
    size            INTEGER NOT NULL,
    content_type    TEXT,
    extension       TEXT,
    downloaded_at   TEXT NOT NULL,
    parse_status    TEXT NOT NULL DEFAULT 'pending',  -- pending | parsed | failed | unsupported
    parser_version  TEXT,
    parsed_at       TEXT,
    parse_error     TEXT
);
CREATE INDEX IF NOT EXISTS files_parse_status ON files(parse_status);

CREATE TABLE IF NOT EXISTS document_versions (
    document_key  TEXT NOT NULL REFERENCES documents(key),
    sha256        TEXT NOT NULL REFERENCES files(sha256),
    fetched_at    TEXT NOT NULL,
    version       INTEGER DEFAULT 1,
    PRIMARY KEY (document_key, sha256)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        for col, col_def in [
            ("parse_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("html_hash", "TEXT"),
            ("parsed_at", "TEXT"),
            ("parse_error", "TEXT"),
            ("consecutive_missing", "INTEGER NOT NULL DEFAULT 0"),
            ("removed_at", "TEXT"),
        ]:
            with contextlib.suppress(sqlite3.OperationalError):
                self.conn.execute(f"ALTER TABLE pages ADD COLUMN {col} {col_def}")

        for col, col_def in [
            ("version", "INTEGER NOT NULL DEFAULT 1"),
            ("previous_sha256", "TEXT"),
            ("updated_at", "TEXT"),
            ("consecutive_missing", "INTEGER NOT NULL DEFAULT 0"),
            ("removed_at", "TEXT"),
        ]:
            with contextlib.suppress(sqlite3.OperationalError):
                self.conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_def}")

        with contextlib.suppress(sqlite3.OperationalError):
            self.conn.execute("ALTER TABLE document_versions ADD COLUMN version INTEGER DEFAULT 1")

    def close(self) -> None:
        self.conn.close()

    # --- crawler --------------------------------------------------------------

    def upsert_page(self, page: dict) -> None:
        page = page | {"alternates": json.dumps(page.get("alternates") or {}, ensure_ascii=False)}
        columns = ("url", "site", "status", "depth", "parent", "anchor_text", "title", "lang",
                   "alternates", "html_file", "content_type", "error", "fetched_at")
        with self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO pages ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' * len(columns))})",
                [page.get(c) for c in columns],
            )

    def add_document(self, *, key: str, url: str, site: str, category: str, extension: str,
                     external: bool, source: dict) -> bool:
        """Registers a document link and where it was found. Returns True if the document is new."""
        ts = now()
        with self.conn:
            new = self.conn.execute(
                "INSERT OR IGNORE INTO documents (key, url, site, category, extension, external, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, url, site, category, extension, int(external), ts),
            ).rowcount == 1
            self.conn.execute(
                "INSERT OR IGNORE INTO document_sources "
                "(document_key, found_on, found_on_title, anchor_text, site, depth, via, published, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, source.get("found_on") or "", source.get("found_on_title"), source.get("anchor_text"),
                 site, source.get("depth"), source.get("via"), source.get("published"), ts),
            )
        return new

    def record_crawl_missing(self, site: str, seen_keys: set[str]) -> tuple[int, int]:
        """Called after a crawl run for a site. Any document URL not seen increments consecutive_missing.
        If consecutive_missing >= 2, marks document as 'removed'. Returns (num_missing, num_removed)."""
        ts = now()
        missing_count = 0
        removed_count = 0
        with self.conn:
            docs = self.conn.execute(
                "SELECT key, status, consecutive_missing FROM documents WHERE site = ?", (site,)
            ).fetchall()
            for d in docs:
                k = d["key"]
                if k in seen_keys:
                    self.conn.execute(
                        "UPDATE documents SET consecutive_missing = 0 WHERE key = ?", (k,)
                    )
                else:
                    curr = (d["consecutive_missing"] or 0) + 1
                    if curr >= 2:
                        self.conn.execute(
                            "UPDATE documents SET status = 'removed', consecutive_missing = ?, "
                            "removed_at = COALESCE(removed_at, ?) WHERE key = ?",
                            (curr, ts, k),
                        )
                        removed_count += 1
                    else:
                        self.conn.execute(
                            "UPDATE documents SET consecutive_missing = ? WHERE key = ?",
                            (curr, k),
                        )
                        missing_count += 1
        return missing_count, removed_count

    # --- downloader -----------------------------------------------------------

    def documents_to_download(self, statuses: list[str], sites: list[str] | None, limit: int | None):
        query = f"SELECT * FROM documents WHERE status IN ({', '.join('?' * len(statuses))}) AND status != 'removed'"
        params: list = list(statuses)
        if sites:
            query += f" AND site IN ({', '.join('?' * len(sites))})"
            params += sites
        query += " ORDER BY discovered_at"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def has_file(self, sha256: str) -> bool:
        return self.conn.execute("SELECT 1 FROM files WHERE sha256 = ?", (sha256,)).fetchone() is not None

    def record_download(self, key: str, *, sha256: str, path: str | None, size: int, content_type: str,
                        extension: str, http_status: int, etag: str | None, last_modified: str | None) -> None:
        """Stores a successful download. Handles version incrementing and previous_sha256."""
        ts = now()
        with self.conn:
            if path is not None:
                self.conn.execute(
                    "INSERT INTO files (sha256, path, size, content_type, extension, downloaded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sha256, path, size, content_type, extension, ts),
                )
            row = self.conn.execute(
                "SELECT sha256, version FROM documents WHERE key = ?", (key,)
            ).fetchone()
            old_sha = row["sha256"] if row else None
            old_ver = (row["version"] or 1) if row else 1

            if old_sha and old_sha != sha256:
                new_ver = old_ver + 1
                prev_sha = old_sha
                updated_at = ts
            else:
                new_ver = old_ver
                prev_sha = None
                updated_at = ts

            self.conn.execute(
                "INSERT OR IGNORE INTO document_versions (document_key, sha256, version, fetched_at) VALUES (?, ?, ?, ?)",
                (key, sha256, new_ver, ts),
            )
            if old_sha and old_sha != sha256:
                self.conn.execute(
                    "UPDATE documents SET status = 'downloaded', sha256 = ?, previous_sha256 = ?, "
                    "version = ?, updated_at = ?, etag = ?, last_modified = ?, "
                    "http_status = ?, error = NULL, checked_at = ?, consecutive_missing = 0, removed_at = NULL "
                    "WHERE key = ?",
                    (sha256, prev_sha, new_ver, updated_at, etag, last_modified, http_status, ts, key),
                )
            else:
                self.conn.execute(
                    "UPDATE documents SET status = 'downloaded', sha256 = ?, "
                    "etag = ?, last_modified = ?, "
                    "http_status = ?, error = NULL, checked_at = ?, consecutive_missing = 0, removed_at = NULL "
                    "WHERE key = ?",
                    (sha256, etag, last_modified, http_status, ts, key),
                )

    def record_download_missing(self, key: str, http_status: int) -> bool:
        """Called when downloader receives 404/410. Increments consecutive_missing.
        If >= 2, status becomes 'removed'. Returns True if marked removed. A document that has a downloaded version
        keeps it (and stays in the index) after the first miss: one 404 can be a hiccup of the site."""
        ts = now()
        with self.conn:
            row = self.conn.execute("SELECT consecutive_missing, status FROM documents WHERE key = ?",
                                    (key,)).fetchone()
            curr = ((row["consecutive_missing"] if row else 0) or 0) + 1
            is_removed = curr >= 2
            keep = row is not None and row["status"] == "downloaded"
            new_status = "removed" if is_removed else "downloaded" if keep else "missing"
            removed_at = ts if is_removed else None
            self.conn.execute(
                "UPDATE documents SET status = ?, http_status = ?, checked_at = ?, "
                "consecutive_missing = ?, removed_at = COALESCE(removed_at, ?) WHERE key = ?",
                (new_status, http_status, ts, curr, removed_at, key),
            )
            return is_removed

    def mark_checked(self, key: str, status: str, *, http_status: int | None = None, error: str | None = None) -> None:
        """A download attempt that got no content. A failure (timeout, 5xx) of a document that has a downloaded
        version keeps it 'downloaded': a site's bad moment must not take a good document out of the index."""
        with self.conn:
            self.conn.execute(
                "UPDATE documents SET status = CASE WHEN ? = 'failed' AND status = 'downloaded' THEN status ELSE ? END, "
                "http_status = ?, error = ?, checked_at = ? WHERE key = ?",
                (status, status, http_status, error, now(), key),
            )

    def mark_not_modified(self, key: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE documents SET http_status = 304, checked_at = ?, consecutive_missing = 0 WHERE key = ?",
                (now(), key),
            )

    # --- parsing --------------------------------------------------------------

    def files_to_parse(self, statuses: list[str], limit: int | None, sha_prefixes: list[str] | None = None):
        query = f"SELECT * FROM files WHERE parse_status IN ({', '.join('?' * len(statuses))})"
        params: list = list(statuses)
        if sha_prefixes:
            query += " AND (" + " OR ".join("sha256 LIKE ?" for _ in sha_prefixes) + ")"
            params += [f"{p}%" for p in sha_prefixes]
        query += " ORDER BY downloaded_at"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def file_sources(self, sha256: str) -> list[dict]:
        """Every document URL with this content and every page it was found on."""
        rows = self.conn.execute(
            "SELECT d.url, d.site, d.category, s.found_on, s.found_on_title, s.anchor_text, s.via, s.published "
            "FROM documents d JOIN document_sources s ON s.document_key = d.key "
            "WHERE d.sha256 = ? ORDER BY s.depth IS NULL, s.depth, s.discovered_at",
            (sha256,),
        ).fetchall()
        return [{k: v for k, v in dict(row).items() if v not in (None, "")} for row in rows]


    def mark_parsed(self, sha256: str, status: str, *, parser_version: str | None = None,
                    error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE files SET parse_status = ?, parser_version = ?, parse_error = ?, parsed_at = ? "
                "WHERE sha256 = ?",
                (status, parser_version, error, now(), sha256),
            )

    # --- pages parsing --------------------------------------------------------

    def pages_to_parse(self, sites: list[str] | None = None, limit: int | None = None, reparse: bool = False):
        query = "SELECT * FROM pages WHERE status < 400 AND html_file IS NOT NULL"
        params: list = []
        if not reparse:
            query += " AND (parse_status = 'pending' OR parse_status IS NULL)"
        if sites:
            query += f" AND site IN ({', '.join('?' * len(sites))})"
            params += sites
        query += " ORDER BY fetched_at"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def site_pages(self, site: str) -> list:
        """Every crawled page of a site with its HTML on disk (context for boilerplate detection)."""
        return self.conn.execute("SELECT * FROM pages WHERE site = ? AND status < 400 AND html_file IS NOT NULL",
                                 (site,)).fetchall()

    def mark_page_parsed(self, url: str, status: str, *, html_hash: str | None = None,
                         error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE pages SET parse_status = ?, html_hash = ?, parsed_at = ?, parse_error = ? WHERE url = ?",
                (status, html_hash, now(), error, url),
            )

    def status_counts(self) -> dict[str, dict[str, int]]:
        docs = dict(self.conn.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall())
        files = dict(self.conn.execute("SELECT parse_status, COUNT(*) FROM files GROUP BY parse_status").fetchall())
        pages = dict(self.conn.execute("SELECT parse_status, COUNT(*) FROM pages GROUP BY parse_status").fetchall())
        return {"documents": docs, "files": files, "pages": pages}

    def active_file_documents(self) -> list[dict]:
        """Returns active (non-removed) downloaded & parsed documents.
        Deduplicates identical file content (sha256): primary URL is the earliest discovered,
        all other URLs are added to sources."""
        from common.urls import url_key

        rows = self.conn.execute(
            """
            SELECT d.key, d.url, d.site, d.category, d.extension, d.sha256,
                   d.version, d.previous_sha256, d.updated_at, d.discovered_at,
                   f.path, f.size, f.content_type, f.parse_status, f.parser_version
            FROM documents d
            JOIN files f ON d.sha256 = f.sha256
            WHERE d.status = 'downloaded' AND d.sha256 IS NOT NULL
              AND f.parse_status = 'parsed'
            ORDER BY d.sha256, d.discovered_at ASC
            """
        ).fetchall()

        by_sha: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_sha.setdefault(r["sha256"], []).append(r)

        result = []
        for sha, doc_rows in by_sha.items():
            primary = doc_rows[0]
            primary_key = primary["key"] or url_key(primary["url"])
            doc_id = f"file:{primary_key}"

            sources = self.file_sources(sha)

            result.append({
                "doc_id": doc_id,
                "primary_url": primary["url"],
                "url_key": primary_key,
                "sha256": sha,
                "site": primary["site"],
                "category": primary["category"],
                "extension": primary["extension"],
                "version": primary["version"] or 1,
                "previous_sha256": primary["previous_sha256"],
                "updated_at": primary["updated_at"],
                "sources": sources,
            })
        return result

    def active_page_documents(self) -> list[dict]:
        """Returns all parsed active (non-removed) pages."""
        from common.urls import url_key

        rows = self.conn.execute(
            """
            SELECT url, site, html_hash, title, lang, html_file, fetched_at
            FROM pages
            WHERE status < 400 AND parse_status = 'parsed'
              AND html_file IS NOT NULL
            ORDER BY fetched_at ASC
            """
        ).fetchall()
        result = []
        for r in rows:
            ukey = url_key(r["url"])
            result.append({
                "doc_id": f"page:{ukey}",
                "url": r["url"],
                "url_key": ukey,
                "site": r["site"],
                "html_hash": r["html_hash"],
                "title": r["title"],
                "lang": r["lang"],
                "html_file": r["html_file"],
                "fetched_at": r["fetched_at"],
            })
        return result

