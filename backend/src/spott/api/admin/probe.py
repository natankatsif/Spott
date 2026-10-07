"""What a pasted link is, without an LLM: its site, whether robots.txt lets us crawl it, a document or a page (and its
title and description, for the category), and the crawl settings that fit it."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser

from spott.core.sources import DEFAULTS, DOCUMENT_TYPES, EXCLUDED_SITES, USER_AGENT, is_document_link

from ..errors import ApiException

PROBE_TIMEOUT_S = 8.0
MAX_HEAD_BYTES = 300_000  # enough of a page for its <title> and meta description


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def normalize_url(url: str) -> str:
    """Scheme added when missing, host lowercased, a trailing slash dropped (except the root)."""
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise ApiException(422, "validation_error", "url: only http(s) links to a website or a document")
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def is_root(url: str) -> bool:
    return urlsplit(url).path in ("", "/")


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether robots.txt lets the crawler fetch this URL. No robots.txt (4xx) allows; a site that can't be reached
    at all counts as allowed here — the page fetch that follows reports it."""
    parts = urlsplit(url)
    try:
        resp = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=PROBE_TIMEOUT_S)
    except httpx.HTTPError:
        return True
    if resp.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


@dataclass
class Probe:
    content_type: str
    final_url: str
    title: str | None
    description: str | None


async def probe(client: httpx.AsyncClient, url: str) -> Probe:
    """HEAD, then GET if the server doesn't answer HEAD or it's a page (for its title), 8 s, redirects followed.
    Unreachable or an error status → 422."""
    try:
        resp = await client.head(url, timeout=PROBE_TIMEOUT_S)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        body = b""
        if resp.status_code >= 400 or ctype not in DOCUMENT_TYPES:
            async with client.stream("GET", url, timeout=PROBE_TIMEOUT_S) as resp:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code < 400 and ctype not in DOCUMENT_TYPES:
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) >= MAX_HEAD_BYTES:
                            break
    except httpx.HTTPError as e:
        raise ApiException(422, "validation_error", f"The site doesn't answer ({type(e).__name__})") from e
    if resp.status_code >= 400:
        raise ApiException(422, "validation_error", f"The site answered {resp.status_code} for this link")
    title = description = None
    if body:
        tree = HTMLParser(body.decode(resp.encoding or "utf-8", errors="replace"))
        node = tree.css_first("title")
        title = " ".join(node.text().split())[:200] if node else None
        meta = tree.css_first('meta[name="description"]') or tree.css_first('meta[property="og:description"]')
        description = (meta.attributes.get("content") or "").strip()[:500] if meta else None
    return Probe(ctype, str(resp.url), title or None, description or None)


def crawl_settings(url: str, kind: str) -> tuple[int | None, int | None]:
    """(depth, max pages): a site root like sites.toml's defaults; a deeper path a shallow crawl of that path."""
    if kind == "document":
        return None, None
    return (DEFAULTS["max_depth"], DEFAULTS["max_pages"]) if is_root(url) else (2, DEFAULTS["max_pages"])


@dataclass
class Link:
    """A pasted link, inspected: its site, a site or a document, whether we may crawl it, what its page says."""
    url: str
    site_id: str
    kind: str  # site | document
    blocked: bool  # the site is excluded, or its robots.txt forbids the crawler
    title: str | None = None
    text: str = ""  # the page's title and description: the category's keywords are looked for in them


async def inspect(client: httpx.AsyncClient, url: str, kind: str | None = None) -> Link:
    """robots.txt first: nothing is fetched from a site that forbids it. Then the kind (PDF/DOC/DOCX by content type
    or extension, else a site), unless `kind` is given, and the page's title and description."""
    site_id = site_of(url)
    if site_id in EXCLUDED_SITES or not await robots_allowed(client, url):
        return Link(url, site_id, kind or ("document" if is_document_link(url) else "site"), blocked=True)
    found = await probe(client, url)
    document = found.content_type in DOCUMENT_TYPES or is_document_link(url)
    return Link(url, site_id, kind or ("document" if document else "site"), blocked=False, title=found.title,
                text=" ".join(x for x in (found.title, found.description) if x))
