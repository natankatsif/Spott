"""Breadth-first crawler for a single site.

Output (data/crawl/<site id>/):
    pages.jsonl      one record per fetched HTML page (title, lang, hreflang pairs, parent, depth)
    documents.jsonl  one record per discovered document link (pdf/doc/xls/..., with provenance)
    html/            raw HTML of every page, named by hash of the URL
    state.json       queue + seen sets, for --resume
"""

import asyncio
import hashlib
import html
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser

from .config import Site
from .urls import (
    bare_host,
    extension,
    is_document_type,
    is_document_url,
    is_external_doc_host,
    is_skipped,
    normalize,
    url_key,
)

log = logging.getLogger(__name__)

USER_AGENT = "ChisinauAssistantBot/0.1 (+GigaHack 2026; municipal RAG research crawler)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
MAX_BODY_BYTES = 15 * 1024 * 1024
RETRIES = 2
RETRY_STATUSES = {429, 500, 502, 503, 504}
STATE_EVERY = 25
WP_MEDIA_MAX_PAGES = 100

# Elements whose attribute can point at a page or a document.
LINK_SELECTORS = (("a[href]", "href"), ("iframe[src]", "src"), ("embed[src]", "src"), ("object[data]", "data"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tls_failed(error: httpx.TransportError) -> bool:
    return isinstance(error, httpx.ConnectError) and "CERTIFICATE_VERIFY_FAILED" in str(error)


class SiteCrawler:
    def __init__(
        self,
        site: Site,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        out_dir: Path,
        *,
        respect_robots: bool = True,
    ):
        self.site = site
        self.client = client
        self.insecure_client = insecure_client
        self.out = out_dir / site.id
        self.respect_robots = respect_robots and not site.ignore_robots
        self.delay = site.delay
        self.robots: RobotFileParser | None = None
        self.allowed_hosts = {bare_host(urlsplit(u).hostname or "") for u in site.start_urls}
        # (url, depth, parent url, anchor text)
        self.queue: deque[tuple[str, int, str | None, str]] = deque()
        self.seen: set[str] = set()
        self.docs_seen: set[str] = set()
        self.titles: dict[str, str] = {}
        self.stats = {"pages": 0, "errors": 0, "documents": 0, "wp_media": 0, "robots_blocked": 0}

    async def run(self, resume: bool = False) -> dict:
        started = time.monotonic()
        (self.out / "html").mkdir(parents=True, exist_ok=True)
        resumed = resume and self._load_state()
        mode = "a" if resumed else "w"
        with (
            open(self.out / "pages.jsonl", mode, encoding="utf-8") as self.pages_f,
            open(self.out / "documents.jsonl", mode, encoding="utf-8") as self.docs_f,
        ):
            await self._load_robots()
            if not resumed:
                for url in self.site.start_urls:
                    self._enqueue(url, 0, None, "")
                await self._discover_wp_media()

            steps = 0
            try:
                while self.queue and self.stats["pages"] < self.site.max_pages:
                    await self._visit(*self.queue.popleft())
                    steps += 1
                    if steps % STATE_EVERY == 0:
                        self._save_state()
                        log.info("%s: pages=%d docs=%d queue=%d", self.site.id,
                                 self.stats["pages"], self.stats["documents"], len(self.queue))
            finally:
                self._save_state()

        return self.stats | {"queue_left": len(self.queue), "seconds": round(time.monotonic() - started)}

    # --- crawling -------------------------------------------------------------

    def _enqueue(self, url: str, depth: int, parent: str | None, anchor: str) -> None:
        url = normalize(url)
        if not url:
            return
        key = url_key(url)
        if key in self.seen:
            return
        self.seen.add(key)
        self.queue.append((url, depth, parent, anchor))

    async def _visit(self, url: str, depth: int, parent: str | None, anchor: str) -> None:
        if not self._allowed_by_robots(url):
            self.stats["robots_blocked"] += 1
            return

        page = {"url": url, "depth": depth, "parent": parent, "anchor_text": anchor, "fetched_at": now()}
        try:
            status, final_url, ctype, body = await self._fetch(url, HTML_TYPES)
        except httpx.HTTPError as e:
            self.stats["pages"] += 1
            self.stats["errors"] += 1
            self._write(self.pages_f, page | {"error": f"{type(e).__name__}: {e}"})
            return

        final = normalize(final_url) or url
        final_host = bare_host(urlsplit(final).hostname or "")
        if final_host not in self.allowed_hosts:
            if depth > 0:
                return  # redirected off-site
            self.allowed_hosts.add(final_host)  # start URL redirects to the canonical domain
        self.seen.add(url_key(final))

        if status < 400 and is_document_type(ctype):
            # Link without a document extension that serves a file (e.g. /download?id=12).
            self._add_document(final, parent, anchor, depth, via="content-type")
            return
        if status < 400 and body is None:
            return  # some other non-HTML resource

        self.stats["pages"] += 1
        page |= {"final_url": final, "status": status, "content_type": ctype}
        if status >= 400:
            self.stats["errors"] += 1
            self._write(self.pages_f, page)
            return

        self._write(self.pages_f, page | self._process_html(final, depth, body))

    def _process_html(self, url: str, depth: int, body: bytes) -> dict:
        tree = HTMLParser(body)
        html_file = f"html/{hashlib.sha1(url_key(url).encode()).hexdigest()[:20]}.html"
        (self.out / html_file).write_bytes(body)

        base = url
        if (base_node := tree.css_first("base[href]")) is not None:
            base = urljoin(url, base_node.attributes.get("href") or "")
        title_node = tree.css_first("title")
        title = " ".join(title_node.text().split()) if title_node is not None else ""
        self.titles[url] = title
        html_node = tree.css_first("html")
        lang = (html_node.attributes.get("lang") or "") if html_node is not None else ""

        # hreflang pairs link ro/ru/en versions of the same page.
        alternates = {}
        for node in tree.css('link[rel="alternate"][hreflang]'):
            href = normalize(urljoin(base, node.attributes.get("href") or ""))
            if href and node.attributes.get("hreflang"):
                alternates[node.attributes["hreflang"]] = href

        links_enqueued = docs_found = 0
        for selector, attr in LINK_SELECTORS:
            for node in tree.css(selector):
                target = normalize(urljoin(base, (node.attributes.get(attr) or "").strip()))
                if not target:
                    continue
                if node.tag == "a":
                    anchor = " ".join(node.text(separator=" ").split())[:300]
                else:
                    anchor = node.attributes.get("title") or ""
                internal = bare_host(urlsplit(target).hostname or "") in self.allowed_hosts
                if is_document_url(target) or (not internal and is_external_doc_host(target)):
                    self._add_document(target, url, anchor, depth + 1, via=node.tag, external=not internal)
                    docs_found += 1
                elif internal and depth < self.site.max_depth and not is_skipped(target):
                    before = len(self.queue)
                    self._enqueue(target, depth + 1, url, anchor)
                    links_enqueued += len(self.queue) - before

        return {
            "title": title,
            "lang": lang,
            "alternates": alternates,
            "html_file": html_file,
            "links_enqueued": links_enqueued,
            "documents_found": docs_found,
        }

    def _add_document(
        self,
        url: str,
        found_on: str | None,
        anchor: str,
        depth: int | None,
        *,
        via: str,
        external: bool = False,
        **extra,
    ) -> None:
        key = url_key(url)
        if key in self.docs_seen:
            return
        self.docs_seen.add(key)
        self.stats["documents"] += 1
        self._write(self.docs_f, {
            "url": url,
            "site": self.site.id,
            "category": self.site.category,
            "extension": extension(url),
            "found_on": found_on,
            "found_on_title": self.titles.get(found_on or "", ""),
            "anchor_text": anchor,
            "depth": depth,
            "via": via,
            "external": external,
            "discovered_at": now(),
        } | extra)

    async def _discover_wp_media(self) -> None:
        """WordPress exposes its whole media library over REST — finds files no page links to."""
        root = urlsplit(self.site.start_urls[0])
        endpoint = f"{root.scheme}://{root.netloc}/wp-json/wp/v2/media"
        if not self._allowed_by_robots(endpoint):
            return
        for page in range(1, WP_MEDIA_MAX_PAGES + 1):
            try:
                status, _, _, body = await self._fetch(
                    f"{endpoint}?media_type=application&per_page=100&page={page}", ("application/json",)
                )
                items = json.loads(body) if status == 200 and body else None
            except (httpx.HTTPError, ValueError):
                return
            if not isinstance(items, list) or not items:
                return
            for item in items:
                src = normalize(item.get("source_url") or "")
                if not src or not (is_document_type(item.get("mime_type") or "") or is_document_url(src)):
                    continue
                before = self.stats["documents"]
                self._add_document(
                    src,
                    item.get("link"),
                    html.unescape((item.get("title") or {}).get("rendered") or ""),
                    None,
                    via="wp-media",
                    external=bare_host(urlsplit(src).hostname or "") not in self.allowed_hosts,
                    published=item.get("date"),
                )
                self.stats["wp_media"] += self.stats["documents"] - before

    # --- HTTP -----------------------------------------------------------------

    async def _fetch(self, url: str, read_types: tuple[str, ...]) -> tuple[int, str, str, bytes | None]:
        """GET with retries. The body is read only when the content type is in read_types."""
        attempt = 0
        while True:
            try:
                status, final_url, ctype, body = await self._request(url, read_types)
            except httpx.TransportError as e:
                if tls_failed(e) and self.client is not self.insecure_client:
                    log.warning("%s: TLS certificate invalid, continuing without verification", self.site.id)
                    self.client = self.insecure_client
                    continue
                if attempt >= RETRIES:
                    raise
            else:
                if status not in RETRY_STATUSES or attempt >= RETRIES:
                    return status, final_url, ctype, body
            attempt += 1
            await asyncio.sleep(2**attempt)

    async def _request(self, url: str, read_types: tuple[str, ...]) -> tuple[int, str, str, bytes | None]:
        try:
            async with self.client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                body = None
                if resp.status_code < 400 and ctype in read_types:
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                return resp.status_code, str(resp.url), ctype, body
        finally:
            await asyncio.sleep(self.delay)

    async def _load_robots(self) -> None:
        if not self.respect_robots:
            return
        root = urlsplit(self.site.start_urls[0])
        try:
            status, _, _, body = await self._fetch(f"{root.scheme}://{root.netloc}/robots.txt", ("text/plain",))
        except httpx.HTTPError:
            return
        if status >= 400 or body is None:
            return
        self.robots = RobotFileParser()
        self.robots.parse(body.decode("utf-8", "replace").splitlines())
        if crawl_delay := self.robots.crawl_delay(USER_AGENT):
            self.delay = max(self.delay, float(crawl_delay))

    def _allowed_by_robots(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(USER_AGENT, url)

    # --- persistence ----------------------------------------------------------

    @staticmethod
    def _write(f, record: dict) -> None:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()

    def _save_state(self) -> None:
        state = {
            "queue": list(self.queue),
            "seen": sorted(self.seen),
            "docs_seen": sorted(self.docs_seen),
            "allowed_hosts": sorted(self.allowed_hosts),
            "stats": self.stats,
            "saved_at": now(),
        }
        tmp = self.out / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.out / "state.json")

    def _load_state(self) -> bool:
        path = self.out / "state.json"
        if not path.exists():
            return False
        state = json.loads(path.read_text(encoding="utf-8"))
        self.queue = deque(tuple(item) for item in state["queue"])
        self.seen = set(state["seen"])
        self.docs_seen = set(state["docs_seen"])
        self.allowed_hosts = set(state["allowed_hosts"])
        self.stats = state["stats"]
        return True
