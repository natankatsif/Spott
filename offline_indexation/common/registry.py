"""SQLite registry shared by all offline indexation stages (data/registry.sqlite).

    pages              HTML pages fetched by the crawler
    documents          one row per document URL; status of its download
    document_sources   every page where a document link was found (provenance for citations)
    files              unique downloaded contents, keyed by SHA-256; status of parsing
    document_versions  history of contents seen at each document URL
"""

import json
import sqlite3
from datetime import datetime, timezone
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
    status         TEXT NOT NULL DEFAULT 'discovered',  -- discovered | downloaded | not_a_file | failed
    sha256         TEXT REFERENCES files(sha256),       -- current content
    etag           TEXT,
    last_modified  TEXT,
    http_status    INTEGER,
    error          TEXT,
    discovered_at  TEXT NOT NULL,
    checked_at     TEXT                      -- last download attempt
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
    PRIMARY KEY (document_key, sha256)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

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

    # --- downloader -----------------------------------------------------------

    def documents_to_download(self, statuses: list[str], sites: list[str] | None, limit: int | None):
        query = f"SELECT * FROM documents WHERE status IN ({', '.join('?' * len(statuses))})"
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
        """Stores a successful download. `path` is None when the content was already known."""
        ts = now()
        with self.conn:
            if path is not None:
                self.conn.execute(
                    "INSERT INTO files (sha256, path, size, content_type, extension, downloaded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sha256, path, size, content_type, extension, ts),
                )
            self.conn.execute(
                "INSERT OR IGNORE INTO document_versions (document_key, sha256, fetched_at) VALUES (?, ?, ?)",
                (key, sha256, ts),
            )
            self.conn.execute(
                "UPDATE documents SET status = 'downloaded', sha256 = ?, etag = ?, last_modified = ?, "
                "http_status = ?, error = NULL, checked_at = ? WHERE key = ?",
                (sha256, etag, last_modified, http_status, ts, key),
            )

    def mark_checked(self, key: str, status: str, *, http_status: int | None = None, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE documents SET status = ?, http_status = ?, error = ?, checked_at = ? WHERE key = ?",
                (status, http_status, error, now(), key),
            )

    def mark_not_modified(self, key: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE documents SET http_status = 304, checked_at = ? WHERE key = ?", (now(), key))

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
        return [{k: row[k] for k in row.keys() if row[k] not in (None, "")} for row in rows]

    def mark_parsed(self, sha256: str, status: str, *, parser_version: str | None = None,
                    error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE files SET parse_status = ?, parser_version = ?, parse_error = ?, parsed_at = ? "
                "WHERE sha256 = ?",
                (status, parser_version, error, now(), sha256),
            )

    def status_counts(self) -> dict[str, dict[str, int]]:
        docs = dict(self.conn.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall())
        files = dict(self.conn.execute("SELECT parse_status, COUNT(*) FROM files GROUP BY parse_status").fetchall())
        return {"documents": docs, "files": files}
