"""The crawler of a check: changed pages and links new to us only; a complete crawl drops what it didn't reach, a
partial one or one path never counts the rest of the site as missing."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx

from spott.ingest.common.registry import Registry, now
from spott.ingest.crawler.config import Site
from spott.ingest.crawler.site import SiteCrawler


def html(links):
    body = "".join(f'<a href="{u}">{u}</a>' for u in links)
    return f"<html><head><title>t</title></head><body><main>{body}</main></body></html>"


PAGES = {
    "/": html(["/a", "/b", "/doc.pdf"]),
    "/a": html(["/", "/b", "/new"]),
    "/b": html([]),
    "/new": html([]),
}


def crawl(tmp: Path, reg: Registry, start: list[str], *, partial=False, prefix="", depth=2):
    visited = []

    def handler(request):
        visited.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path in PAGES:
            return httpx.Response(200, text=PAGES[request.url.path], headers={"content-type": "text/html"})
        return httpx.Response(404, text="no", headers={"content-type": "text/html"})

    site = Site(id="a.md", category="c", start_urls=start, max_depth=depth, max_pages=100, delay=0,
                ignore_robots=False, path_prefix=prefix)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            crawler = SiteCrawler(site, client, client, reg, tmp / "crawl", partial=partial)
            crawler._discover_wp_media = lambda: asyncio.sleep(0)  # no WordPress here
            return await crawler.run()

    stats = asyncio.run(go())
    return visited, stats


def test_a_check_visits_changed_pages_and_only_new_links(tmp_path: Path, registry: Registry):
    reg = registry
    for u in ("https://a.md/", "https://a.md/a", "https://a.md/b"):
        reg.upsert_page({"url": u, "site": "a.md", "status": 200, "html_file": "html/x.html", "fetched_at": now()})
    reg.add_document(key="a.md/doc.pdf", url="https://a.md/doc.pdf", site="a.md", category="c", extension=".pdf",
                     external=False, source={"found_on": "https://a.md/"})
    visited, stats = crawl(tmp_path, reg, ["https://a.md/a"], partial=True, depth=1)
    assert [p for p in visited if p != "/robots.txt"] == ["/a", "/new"]  # not / or /b: known and unchanged
    assert "pages_dropped" not in stats and "missing_documents" not in stats
    assert reg.conn.execute("SELECT consecutive_missing FROM registry_documents").fetchone()[0] == 0


def test_a_path_crawl_never_counts_the_rest_of_the_site_missing(tmp_path: Path, registry: Registry):
    reg = registry
    reg.add_document(key="a.md/doc.pdf", url="https://a.md/doc.pdf", site="a.md", category="c", extension=".pdf",
                     external=False, source={"found_on": "https://a.md/"})
    reg.upsert_page({"url": "https://a.md/old", "site": "a.md", "status": 200, "html_file": "html/o.html",
                     "fetched_at": datetime(2026, 1, 1, tzinfo=UTC)})
    for _ in range(2):  # twice: two misses would remove the document
        crawl(tmp_path, reg, ["https://a.md/b"], prefix="/b")
    assert reg.conn.execute("SELECT status FROM registry_documents").fetchone()[0] == "discovered"
    assert reg.conn.execute("SELECT status FROM registry_pages WHERE url = 'https://a.md/old'").fetchone()[0] == 200


def test_a_complete_crawl_drops_pages_it_did_not_reach(tmp_path: Path, registry: Registry):
    reg = registry
    reg.upsert_page({"url": "https://a.md/gone", "site": "a.md", "status": 200, "html_file": "html/g.html",
                     "fetched_at": datetime(2026, 1, 1, tzinfo=UTC)})
    visited, stats = crawl(tmp_path, reg, ["https://a.md/"])
    assert stats["pages_dropped"] == 1
    assert reg.conn.execute("SELECT status FROM registry_pages WHERE url = 'https://a.md/gone'").fetchone()[0] == 410
    assert reg.conn.execute("SELECT status FROM registry_pages WHERE url = 'https://a.md/new'").fetchone()[0] == 200
