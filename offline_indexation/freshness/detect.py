"""What changed on a site since the last check, the cheapest way the site allows (docs/audit/06-freshness-plan.md).

In order, each method used only when the site supports it:
    wordpress    the REST API: pages and posts modified after `since`, new or modified files in the media library
    sitemap      sitemap.xml: pages with <lastmod> after `since`, and pages we don't know yet
    fingerprint  key pages (start pages and the first level under them) fetched with If-Modified-Since; a 200 is
                 compared by the hash of its main text, not its HTML (menus, dates in the header, tokens change daily)

A sitemap without dates finds new pages only, so it is combined with fingerprints. Nothing here downloads documents
or writes the registry: the caller gets the URLs and decides.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from common.http import HTML_TYPES, USER_AGENT, content_type
from common.urls import bare_host, is_document_url, normalize, url_key

log = logging.getLogger(__name__)

WP_TYPES = ("pages", "posts")
WP_MAX_PAGES = 20  # 100 items each
SITEMAP_PATHS = ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml")
MAX_SITEMAPS = 60
MAX_KEY_PAGES = 40
REDESIGN_SHARE = 0.6  # this share of key pages changed at once (and at least REDESIGN_MIN): a redesign, crawl it all
REDESIGN_MIN = 5


@dataclass
class Changes:
    method: str = ""  # how they were found: wordpress, sitemap, fingerprint, or a combination
    pages: set[str] = field(default_factory=set)  # new or changed pages to crawl
    documents: set[str] = field(default_factory=set)  # new files, or files replaced in the media library
    fingerprints: dict[str, str] = field(default_factory=dict)  # key page → hash of its text, to store
    full_crawl: bool = False  # too much changed at once (a redesign): crawl the whole site instead
    reachable: bool = True  # False when nothing answered: the check didn't happen, so it isn't recorded

    @property
    def empty(self) -> bool:
        return not (self.pages or self.documents or self.full_crawl)


def parse_time(value: str | None) -> datetime | None:
    """ISO 8601 (sitemap, WordPress *_gmt fields without a zone are UTC) → aware datetime; None if unreadable."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def text_fingerprint(html: str) -> str:
    """Hash of a page's main text: what a reader sees, without the page chrome that changes every day."""
    from parsing.html import extract_raw_blocks

    try:
        _, blocks = extract_raw_blocks(html)
        text = " ".join(b["text"] for b in blocks)
    except Exception:  # noqa: BLE001 - a page trafilatura can't read: fall back to its visible text
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html))
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha1(text.encode()).hexdigest()


class Detector:
    def __init__(self, client: httpx.AsyncClient, root: str, *, delay: float = 0.5):
        self.client, self.root, self.delay = client, root.rstrip("/"), delay
        self.host = bare_host(urlsplit(root).hostname or "")
        self.robots: RobotFileParser | None = None
        self.robots_text: str | None = None
        self.answered = 0  # responses that weren't a network error or a 5xx: whether the site was there at all

    async def load_robots(self) -> None:
        """robots.txt once: its rules for every request below, its Crawl-delay, its Sitemap lines."""
        r = await self.get(f"{self.root}/robots.txt")
        if r is not None and r.status_code == 200:
            self.robots_text = r.text
            self.robots = RobotFileParser()
            self.robots.parse(r.text.splitlines())
            if crawl_delay := self.robots.crawl_delay(USER_AGENT):
                self.delay = max(self.delay, float(crawl_delay))

    async def get(self, url: str, **kw) -> httpx.Response | None:
        if self.robots is not None and not self.robots.can_fetch(USER_AGENT, url):
            return None
        try:
            r = await self.client.get(url, **kw)
        except httpx.HTTPError as e:
            log.info("%s: %s", url, e)
            return None
        else:
            self.answered += r.status_code < 500
            return r
        finally:
            await asyncio.sleep(self.delay)

    def own(self, url: str) -> str | None:
        """The URL normalized, if it is on this site."""
        url = normalize(url or "")
        return url if url and bare_host(urlsplit(url).hostname or "") == self.host else None

    # --- WordPress ------------------------------------------------------------

    async def wordpress(self, since: datetime | None) -> Changes | None:
        """Pages, posts and files modified after `since`; None when the site has no public REST API. Dates are
        checked here too: an old WordPress ignores `modified_after` and returns everything."""
        changes = Changes(method="wordpress")
        for kind in (*WP_TYPES, "media"):
            for n in range(1, WP_MAX_PAGES + 1):
                params = {"per_page": 100, "page": n, "orderby": "modified", "order": "desc",
                          "_fields": "link,modified_gmt,source_url,mime_type"}
                if since:
                    params["modified_after"] = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
                if kind == "media":
                    params["media_type"] = "application"
                r = await self.get(f"{self.root}/wp-json/wp/v2/{kind}", params=params)
                items = safe_json(r) if r is not None and r.status_code == 200 else None
                if not isinstance(items, list):
                    if kind == WP_TYPES[0] and n == 1:
                        return None  # no API (404, 401/403 when closed, HTML): another method
                    break  # past the last page (400), or this type is closed
                stop = False
                for item in items:
                    modified = parse_time(item.get("modified_gmt"))
                    if since and modified and modified <= since:
                        stop = True  # newest first: the rest is older
                        continue
                    if kind == "media":
                        mime = item.get("mime_type") or ""
                        src = normalize(item.get("source_url") or "")  # files may live on another host (a CDN)
                        if src and (is_document_url(src) or "pdf" in mime or "word" in mime):
                            changes.documents.add(src)
                    elif link := self.own(item.get("link")):
                        changes.pages.add(link)
                if stop or len(items) < 100:
                    break
        return changes

    # --- sitemap --------------------------------------------------------------

    async def sitemap(self, since: datetime | None, known: set[str]) -> Changes | None:
        """Pages with a <lastmod> after `since` and pages whose URL we don't know; None without a sitemap.
        method is "sitemap" when every entry has a date, "sitemap-new" when only new pages can be told."""
        queue = await self.sitemap_urls()
        if not queue:
            return None
        changes, dated, undated, seen = Changes(), 0, 0, set()
        while queue and len(seen) < MAX_SITEMAPS:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            r = await self.get(url)
            root = parse_xml(r.content) if r is not None and r.status_code == 200 else None
            if root is None:
                continue
            for entry in root:
                loc, lastmod = child_text(entry, "loc"), parse_time(child_text(entry, "lastmod"))
                if not loc:
                    continue
                if local_name(root.tag) == "sitemapindex":
                    if not (since and lastmod and lastmod <= since):  # an unchanged part of the site: skip it
                        queue.append(urljoin(url, loc))
                    continue
                if not (page := self.own(urljoin(url, loc))):
                    continue
                dated, undated = dated + (lastmod is not None), undated + (lastmod is None)
                changed = lastmod is not None and (since is None or lastmod > since)
                if url_key(page) not in known or (since and changed):
                    (changes.documents if is_document_url(page) else changes.pages).add(page)
        if dated + undated == 0:
            return None  # sitemaps that list nothing of this site: as good as none
        changes.method = "sitemap" if dated and not undated else "sitemap-new"
        return changes

    async def sitemap_urls(self) -> list[str]:
        """Sitemaps named in robots.txt, else the usual places; only ones that answer with XML."""
        found = [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", self.robots_text or "")]
        for path in SITEMAP_PATHS:
            if found:
                break
            r = await self.get(self.root + path)
            if r is not None and r.status_code == 200 and parse_xml(r.content) is not None:
                found.append(self.root + path)
        return list(dict.fromkeys(found))

    # --- fingerprints of key pages ------------------------------------------------

    async def fingerprints(self, key_pages: list[str], stored: dict[str, str], since: datetime | None) -> Changes:
        """Key pages whose main text changed since it was last seen. A page seen for the first time only gets its
        fingerprint stored (nothing to compare with). A 304 to If-Modified-Since is unchanged without a body."""
        changes = Changes(method="fingerprint", fingerprints=dict(stored))
        compared = 0
        for url in key_pages[:MAX_KEY_PAGES]:
            headers = {"If-Modified-Since": format_datetime(since.astimezone(UTC), usegmt=True)} if since and url in stored else {}
            r = await self.get(url, headers=headers)
            if r is None or r.status_code == 304 or r.status_code >= 400 or content_type(r) not in HTML_TYPES:
                continue
            modified = r.headers.get("last-modified")
            try:
                if since and url in stored and modified and parsedate_to_datetime(modified) <= since:
                    continue  # the server says it didn't change (and ignored the conditional request)
            except (TypeError, ValueError):
                pass
            fp = text_fingerprint(r.text)
            if url in stored:
                compared += 1
                if stored[url] != fp:
                    changes.pages.add(url)
            changes.fingerprints[url] = fp
        if compared >= REDESIGN_MIN and len(changes.pages) >= REDESIGN_SHARE * compared:
            changes.full_crawl = True
        return changes


async def detect(client: httpx.AsyncClient, root: str, *, since: datetime | None, known: set[str],
                 key_pages: list[str], stored: dict[str, str], delay: float = 0.5) -> Changes:
    """Every method the site supports, cheapest first; fingerprints only where the others can't tell changed
    pages (no WordPress, a sitemap without dates, or none)."""
    d = Detector(client, root, delay=delay)
    await d.load_robots()
    wp = await d.wordpress(since)
    sm = await d.sitemap(since, known) if wp is None or not since else None
    result = Changes()
    methods = []
    for found in (wp, sm):
        if found is not None:
            methods.append(found.method)
            result.pages |= found.pages
            result.documents |= found.documents
    if wp is None and (sm is None or sm.method == "sitemap-new"):
        fp = await d.fingerprints(key_pages, stored, since)
        methods.append("fingerprint")
        result.pages |= fp.pages
        result.fingerprints, result.full_crawl = fp.fingerprints, fp.full_crawl
    else:
        result.fingerprints = dict(stored)
    result.method = "+".join(methods)
    result.reachable = d.answered > 0
    return result


# --- helpers ------------------------------------------------------------------


def safe_json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return None


def parse_xml(body: bytes) -> ET.Element | None:
    if not body or b"<" not in body[:512]:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    return root if local_name(root.tag) in ("urlset", "sitemapindex") else None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if local_name(child.tag) == name:
            return (child.text or "").strip() or None
    return None
