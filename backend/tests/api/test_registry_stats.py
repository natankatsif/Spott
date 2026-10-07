"""What the API reads from the registry tables: per-site counts for the corpus stats and the admin's sources, and the
crawled copy of a page for its preview."""

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from spott.api.preview import PageSource
from spott.api.stats import corpus_totals, registry_counts
from spott.core.db import APP_SQL, init_registry_db


class OnePool:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db(pg):
    init_registry_db(pg)
    pg.execute("INSERT INTO registry_files (sha256, path, downloaded_at, parse_status) VALUES "
               "('f1', 'raw/f1', NOW(), 'pending'), ('f2', 'raw/f2', NOW(), 'parsed')")
    pg.execute("INSERT INTO registry_documents (key, url, site, status, sha256, version, discovered_at) VALUES "
               "('a.md/1', 'https://a.md/1', 'a.md', 'discovered', NULL, 1, NOW()), "
               "('a.md/2', 'https://a.md/2', 'a.md', 'downloaded', 'f1', 2, NOW()), "
               "('a.md/3', 'https://a.md/3', 'a.md', 'downloaded', 'f2', 1, NOW()), "
               "('a.md/4', 'https://a.md/4', 'a.md', 'removed', NULL, 1, NOW())")
    pg.execute("INSERT INTO registry_pages (url, site, status, html_file, fetched_at, parse_status) VALUES "
               "('https://a.md/', 'a.md', 200, 'html/home.html', '2026-10-01T08:00:00+03:00', 'parsed'), "
               "('https://a.md/x/', 'a.md', 200, 'html/x.html', '2026-10-02T09:30:00Z', 'pending'), "
               "('https://b.md/', 'b.md', 404, NULL, '2026-09-30T00:00:00Z', 'pending')")
    return pg


def test_registry_counts(db, tmp_path: Path):
    (tmp_path / "a.md").mkdir()
    (tmp_path / "a.md" / "state.json").write_text(json.dumps({"queue": [["u", 1, None, ""]] * 2}))
    counts = registry_counts(db, tmp_path)
    assert counts["a.md"] == {"pages": 2, "last_crawled": "2026-10-02T09:30:00+00:00", "documents_found": 4,
                              "documents_downloaded": 2, "documents_replaced": 1, "documents_removed": 1,
                              "documents_pending": 1, "files_pending": 1, "pages_pending": 1, "crawl_left": 2}
    assert counts["b.md"] == {"pages": 1, "last_crawled": "2026-09-30T00:00:00+00:00"}


def test_corpus_totals_count_the_site_sources_only(db):
    db.execute(APP_SQL)
    db.execute("INSERT INTO sources (kind, url, site_id) VALUES ('site', 'https://a.md/', 'a.md'), "
               "('site', 'https://c.md/', 'c.md'), ('document', 'https://x.md/d.pdf', 'x.md')")
    db.execute("CREATE TABLE documents (doc_id TEXT, kind TEXT, site TEXT); CREATE TABLE chunks (chunk_id TEXT, site TEXT); "
               "CREATE TABLE lines (line_id TEXT)")
    db.execute("INSERT INTO documents VALUES ('page:c.md/', 'page', 'c.md'), ('file:c.md/d.pdf', 'file', 'c.md')")
    db.execute("INSERT INTO chunks VALUES ('k1', 'a.md'), ('k2', 'c.md'), ('k3', 'b.md')")
    db.execute("INSERT INTO lines VALUES ('l1'), ('l2')")
    # a.md from the registry; c.md, restored from a dump and unknown to the registry, from the index; b.md isn't a source
    assert corpus_totals(OnePool(db)).model_dump() == {
        "sites_total": 2, "sites_indexed": 2, "pages": 2 + 1, "documents_found": 4 + 1, "documents_downloaded": 2 + 1,
        "chunks": 2, "lines": 2, "documents_replaced": 1, "documents_removed": 1}


def test_registry_counts_before_the_registry_exists(pg, tmp_path: Path):
    assert registry_counts(pg, tmp_path) == {}


def test_the_crawled_copy_of_a_page(db, tmp_path: Path):
    (tmp_path / "crawl" / "a.md" / "html").mkdir(parents=True)
    (tmp_path / "crawl" / "a.md" / "html" / "x.html").write_text("<p>x</p>", encoding="utf-8")
    pages = PageSource(None, OnePool(db), tmp_path)
    assert pages.crawled("https://a.md/x") == ("<p>x</p>", "2026-10-02T09:30:00+00:00")  # stored with a slash
    assert pages.crawled("https://a.md/") is None  # in the registry, its file is not on this machine
    assert asyncio.run(pages.get("https://a.md/x")) == ("<p>x</p>", "crawl", "2026-10-02T09:30:00+00:00")
    assert PageSource(None, None, tmp_path).crawled("https://a.md/x") is None
