"""The worker's queries over the registry: what each source has left to do (the autopilot picks by it) and what a
site has after a job."""

import json
from pathlib import Path

import pytest

from spott.core.db import APP_SQL
from spott.ingest.common.registry import Registry
from spott.ingest.worker.__main__ import PgJobStore
from spott.ingest.worker.schedule import SiteWork


class Store(PgJobStore):
    def __init__(self, conn, crawl_dir: Path):  # the real one connects to the configured database
        self.conn, self.crawl_dir = conn, crawl_dir


@pytest.fixture
def store(registry: Registry, tmp_path: Path) -> Store:
    registry.conn.execute(APP_SQL)
    for site in ("a.md", "b.md", "c.md"):
        registry.conn.execute("INSERT INTO sources (kind, url, site_id) VALUES ('site', %s, %s)",
                              (f"https://{site}/", site))
    return Store(registry.conn, tmp_path / "crawl")


def document(registry: Registry, site: str, name: str, sha: str | None = None, parsed: bool = False) -> None:
    key = f"{site}/{name}"
    registry.add_document(key=key, url=f"https://{key}", site=site, extension=".pdf",
                          source={"found_on": f"https://{site}/"})
    if sha:
        registry.record_download(key, sha256=sha, path=f"raw/{sha}.pdf", extension=".pdf", http_status=200,
                                 etag=None, last_modified=None)
        if parsed:
            registry.mark_parsed(sha, "parsed")


def test_work_left_per_source(store: Store, registry: Registry):
    document(registry, "a.md", "1.pdf")
    document(registry, "a.md", "2.pdf")
    document(registry, "a.md", "3.pdf", sha="3" * 64)  # downloaded, not parsed
    document(registry, "a.md", "4.pdf", sha="4" * 64, parsed=True)
    registry.upsert_page({"url": "https://a.md/", "site": "a.md", "status": 200, "html_file": "html/1.html",
                          "fetched_at": "2026-10-01T00:00:00Z"})
    (store.crawl_dir / "b.md").mkdir(parents=True)
    (store.crawl_dir / "b.md" / "state.json").write_text(json.dumps({"queue": [["u", 1, None, ""]] * 3}))
    store.conn.execute("INSERT INTO jobs (source_id, kind) SELECT id, 'crawl' FROM sources WHERE site_id = 'c.md'")

    a, b = store.backlog_candidates()  # c.md has a job of its own queued: left alone
    assert a == SiteWork(id=a.id, site_id="a.md", undownloaded=2, files_pending=1, pages_pending=1)
    assert b == SiteWork(id=b.id, site_id="b.md", queue_left=3)


def test_site_stats(store: Store, registry: Registry):
    store.conn.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, site TEXT)")
    store.conn.execute("CREATE TABLE lines (line_id TEXT PRIMARY KEY, chunk_id TEXT)")
    store.conn.execute("INSERT INTO chunks VALUES ('c1', 'a.md'), ('c2', 'b.md')")
    store.conn.execute("INSERT INTO lines VALUES ('l1', 'c1'), ('l2', 'c1'), ('l3', 'c2')")
    document(registry, "a.md", "1.pdf")
    document(registry, "a.md", "2.pdf", sha="2" * 64, parsed=True)
    registry.upsert_page({"url": "https://a.md/", "site": "a.md", "status": 200, "fetched_at": "2026-10-01T00:00:00Z"})

    assert store.site_stats("a.md") == {"pages": 1, "documents_found": 2, "documents_downloaded": 1,
                                        "files_parsed": 1, "chunks": 1, "lines": 2}
    assert store.site_stats("none.md") == {"pages": 0, "documents_found": 0, "documents_downloaded": 0,
                                           "files_parsed": 0, "chunks": 0, "lines": 0}
