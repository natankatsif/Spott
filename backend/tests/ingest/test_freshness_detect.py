"""Finding what changed on fake sites of every kind we met on the 39 real ones (docs/history/audit/06-freshness-plan.md):
WordPress (new and old, closed API), sitemaps with and without dates, a sitemap index, only Last-Modified headers,
nothing at all, a redesign."""

import asyncio
from datetime import UTC, datetime

import httpx

from spott.ingest.common.urls import url_key
from spott.ingest.freshness.detect import Detector, detect, text_fingerprint

SINCE = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
OLD, NEW = "2026-09-01T10:00:00", "2026-09-25T10:00:00"


def page(text: str, extra: str = "") -> str:
    body = f"<p>{text} " + "Informație publică despre serviciile direcției pentru cetățeni. " * 4 + "</p>"
    return f"<html><head><title>t</title></head><body><header>{extra}</header><main><h1>Pagina</h1>{body}</main></body></html>"


class Site:
    """A fake site: path → (status, body, headers). Records every request."""

    def __init__(self, routes: dict):
        self.routes, self.requests = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = request.url.path + (("?" + request.url.query.decode()) if request.url.query else "")
        route = self.routes.get(key) or self.routes.get(request.url.path)
        if callable(route):
            route = route(request)
        if route is None:
            return httpx.Response(404, text="not found")
        status, body, headers = route if len(route) == 3 else (*route, {})
        if isinstance(body, (list, dict)):
            return httpx.Response(status, json=body, headers=headers)
        ctype = "application/xml" if isinstance(body, str) and body.lstrip().startswith("<?xml") else "text/html"
        return httpx.Response(status, text=body, headers={"content-type": ctype} | headers)


def run(site: Site, coro_fn):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(site), base_url="https://a.md") as client:
            return await coro_fn(client)

    return asyncio.run(go())


def urlset(entries):
    rows = "".join(f"<url><loc>{u}</loc>{f'<lastmod>{m}</lastmod>' if m else ''}</url>" for u, m in entries)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


# --- WordPress --------------------------------------------------------------------


def wp_routes(pages, posts=(), media=(), honours_filter=True):
    def listing(items):
        def answer(request):
            after = request.url.params.get("modified_after")
            got = [i for i in items if not (honours_filter and after and i["modified_gmt"] <= after)]
            n = int(request.url.params.get("page", 1))
            return (200, got) if n == 1 else (400, {"code": "rest_post_invalid_page_number"})
        return answer
    return {"/wp-json/wp/v2/pages": listing(pages), "/wp-json/wp/v2/posts": listing(posts),
            "/wp-json/wp/v2/media": listing(media)}


def test_wordpress_finds_modified_pages_posts_and_files():
    site = Site(wp_routes(
        pages=[{"link": "https://a.md/servicii/", "modified_gmt": NEW}, {"link": "https://a.md/despre/", "modified_gmt": OLD}],
        posts=[{"link": "https://a.md/2026/09/anunt/", "modified_gmt": NEW}],
        media=[{"source_url": "https://a.md/wp-content/uploads/decizia.pdf", "mime_type": "application/pdf",
                "modified_gmt": NEW}]))
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).wordpress(SINCE))
    assert ch.pages == {"https://a.md/servicii/", "https://a.md/2026/09/anunt/"}
    assert ch.documents == {"https://a.md/wp-content/uploads/decizia.pdf"}
    assert all(r.url.params["modified_after"] == "2026-09-20T00:00:00" for r in site.requests)


def test_an_old_wordpress_that_ignores_the_filter_is_filtered_by_date_here():
    site = Site(wp_routes(pages=[{"link": "https://a.md/nou/", "modified_gmt": NEW},
                                 {"link": "https://a.md/vechi/", "modified_gmt": OLD}], honours_filter=False))
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).wordpress(SINCE))
    assert ch.pages == {"https://a.md/nou/"}


def test_a_closed_or_missing_api_is_no_wordpress():
    for status, body in ((401, {"code": "rest_not_logged_in"}), (404, "nope"), (200, "<html>home</html>")):
        site = Site({"/wp-json/wp/v2/pages": (status, body)})
        assert run(site, lambda c: Detector(c, "https://a.md", delay=0).wordpress(SINCE)) is None


# --- sitemaps -------------------------------------------------------------------


def test_a_dated_sitemap_gives_changed_and_new_pages_and_skips_unchanged_parts_of_an_index():
    index = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             f"<sitemap><loc>https://a.md/s-pages.xml</loc><lastmod>{NEW}</lastmod></sitemap>"
             f"<sitemap><loc>https://a.md/s-old.xml</loc><lastmod>{OLD}</lastmod></sitemap></sitemapindex>")
    site = Site({
        "/robots.txt": (200, "User-agent: *\nSitemap: https://a.md/index.xml\n"),
        "/index.xml": (200, index),
        "/s-pages.xml": (200, urlset([("https://a.md/changed", NEW), ("https://a.md/same", OLD),
                                      ("https://www.a.md/brand-new", OLD), ("https://a.md/f.pdf", NEW),
                                      ("https://other.md/x", NEW)])),
        "/s-old.xml": (200, urlset([("https://a.md/never-asked", OLD)])),
    })
    known = {url_key("https://a.md/changed"), url_key("https://a.md/same")}
    async def go(c):
        d = Detector(c, "https://a.md", delay=0)
        await d.load_robots()  # its Sitemap line points at the index
        return await d.sitemap(SINCE, known)

    ch = run(site, go)
    assert ch.method == "sitemap"
    assert ch.pages == {"https://a.md/changed", "https://www.a.md/brand-new"}  # other hosts ignored
    assert ch.documents == {"https://a.md/f.pdf"}
    assert "/s-old.xml" not in {r.url.path for r in site.requests}  # an unchanged part of the index: not fetched


def test_a_sitemap_without_dates_tells_only_new_pages():
    site = Site({"/sitemap.xml": (200, urlset([("https://a.md/known", None), ("https://a.md/new", None)]))})
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).sitemap(SINCE, {url_key("https://a.md/known")}))
    assert (ch.method, ch.pages) == ("sitemap-new", {"https://a.md/new"})


def test_no_sitemap_anywhere_or_an_html_404_page_is_none():
    site = Site({"/sitemap.xml": (200, "<html>Pagina nu există</html>")})
    assert run(site, lambda c: Detector(c, "https://a.md", delay=0).sitemap(SINCE, set())) is None
    assert run(Site({}), lambda c: Detector(c, "https://a.md", delay=0).sitemap(SINCE, set())) is None


# --- fingerprints -------------------------------------------------------------------


def test_fingerprints_ignore_page_chrome_and_catch_real_changes():
    assert text_fingerprint(page("Taxa este 100 lei.", extra="Azi: 25.09")) == \
        text_fingerprint(page("Taxa este 100 lei.", extra="Azi: 26.09"))  # a date in the header is not a change
    assert text_fingerprint(page("Taxa este 100 lei.")) != text_fingerprint(page("Taxa este 150 lei."))


def test_key_pages_changed_unchanged_304_and_first_seen():
    stored = {"https://a.md/": text_fingerprint(page("Acasă")), "https://a.md/acte": text_fingerprint(page("Acte v1")),
              "https://a.md/stiri": text_fingerprint(page("Știri"))}
    site = Site({
        "/": (200, page("Acasă", extra="ora 12:00")),
        "/acte": (200, page("Acte v2 — decizie nouă")),
        "/stiri": (304, ""),
        "/nou": (200, page("Pagină nouă")),
    })
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).fingerprints(
        ["https://a.md/", "https://a.md/acte", "https://a.md/stiri", "https://a.md/nou"], stored, SINCE))
    assert ch.pages == {"https://a.md/acte"}
    assert "https://a.md/nou" in ch.fingerprints and "https://a.md/nou" not in ch.pages  # first seen: baseline
    sent = {r.url.path: r.headers.get("if-modified-since") for r in site.requests}
    assert sent["/acte"] == "Sun, 20 Sep 2026 00:00:00 GMT" and sent["/nou"] is None
    assert not ch.full_crawl


def test_last_modified_older_than_the_check_is_unchanged_even_without_304():
    stored = {"https://a.md/x": "stale-hash"}
    site = Site({"/x": (200, page("X"), {"last-modified": "Tue, 01 Sep 2026 10:00:00 GMT"})})
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).fingerprints(["https://a.md/x"], stored, SINCE))
    assert ch.pages == set()


def test_a_redesign_asks_for_a_full_crawl():
    urls = [f"https://a.md/p{i}" for i in range(6)]
    stored = {u: text_fingerprint(page(f"vechi {i}")) for i, u in enumerate(urls)}
    site = Site({f"/p{i}": (200, page(f"design nou {i}")) for i in range(6)})
    ch = run(site, lambda c: Detector(c, "https://a.md", delay=0).fingerprints(urls, stored, SINCE))
    assert ch.full_crawl and len(ch.pages) == 6


# --- detect(): which methods run for which site --------------------------------------


def detect_on(site, **kw):
    args = {"since": SINCE, "known": set(), "key_pages": ["https://a.md/"], "stored": {}, "delay": 0} | kw
    return run(site, lambda c: detect(c, "https://a.md", **args))


def test_a_wordpress_site_needs_no_sitemap_or_fingerprints():
    site = Site(wp_routes(pages=[{"link": "https://a.md/x/", "modified_gmt": NEW}]))
    ch = detect_on(site)
    assert (ch.method, ch.pages) == ("wordpress", {"https://a.md/x/"})
    assert not any(r.url.path in ("/sitemap.xml", "/") for r in site.requests)


def test_a_dated_sitemap_site_needs_no_fingerprints():
    site = Site({"/sitemap.xml": (200, urlset([("https://a.md/x", NEW)])), "/": (200, page("Acasă"))})
    ch = detect_on(site, known={url_key("https://a.md/x")})
    assert (ch.method, ch.pages) == ("sitemap", {"https://a.md/x"})
    assert "/" not in {r.url.path for r in site.requests}


def test_an_undated_sitemap_is_combined_with_fingerprints():
    stored = {"https://a.md/": text_fingerprint(page("Acasă v1"))}
    site = Site({"/sitemap.xml": (200, urlset([("https://a.md/new", None)])), "/": (200, page("Acasă v2"))})
    ch = detect_on(site, stored=stored)
    assert ch.method == "sitemap-new+fingerprint"
    assert ch.pages == {"https://a.md/new", "https://a.md/"}


def test_a_site_with_nothing_uses_fingerprints_and_an_unchanged_one_reports_nothing():
    stored = {"https://a.md/": text_fingerprint(page("Acasă"))}
    ch = detect_on(Site({"/": (200, page("Acasă"))}), stored=stored)
    assert ch.method == "fingerprint" and ch.empty


def test_a_site_that_is_down_changes_nothing():
    def down(request):
        raise httpx.ConnectError("unreachable", request=request)

    class Down(Site):
        def __call__(self, request):
            return down(request)

    stored = {"https://a.md/": "h"}
    ch = detect_on(Down({}), stored=stored)
    assert ch.empty and ch.fingerprints == stored and not ch.reachable  # nothing lost, and not counted as checked


def test_robots_rules_and_crawl_delay_are_respected():
    site = Site({"/robots.txt": (200, "User-agent: *\nDisallow: /wp-json/\nDisallow: /privat\nCrawl-delay: 0\n"),
                 "/sitemap.xml": (200, urlset([("https://a.md/x", NEW)])),
                 "/privat": (200, page("secret"))})
    ch = detect_on(site, key_pages=["https://a.md/privat"], stored={"https://a.md/privat": "old"},
                   known={url_key("https://a.md/x")})
    paths = {r.url.path for r in site.requests}
    assert "/wp-json/wp/v2/pages" not in paths and "/privat" not in paths
    assert ch.pages == {"https://a.md/x"} and ch.reachable
