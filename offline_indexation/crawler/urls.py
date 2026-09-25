"""URL normalization and classification for the crawler."""

import posixpath
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

DOC_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
    ".zip", ".rar", ".7z",
}

DOC_CONTENT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-",
    "application/vnd.oasis.opendocument",
    "application/rtf",
    "text/rtf",
    "text/csv",
    "application/zip",
    "application/x-zip",
    "application/x-rar",
    "application/vnd.rar",
    "application/x-7z",
)

# Assets that are neither pages nor documents.
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".mp4", ".mp3", ".avi", ".mov", ".webm", ".wav", ".ogg",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
}

# Hosts where public documents are often published outside the site itself.
EXTERNAL_DOC_HOSTS = (
    "drive.google.com",
    "docs.google.com",
    "actelocale.gov.md",
    "legis.md",
)

TRACKING_PARAM = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|_ga|mc_cid|mc_eid)$", re.I)

# Crawler traps and pages with no content: admin, search, feeds, calendars, print views.
TRAP = re.compile(
    r"/wp-(admin|login|json)|/xmlrpc|/feed/?$|/cdn-cgi/|/(login|logout|register)\b"
    r"|/tag/|/author/|/calendar|/print/"
    r"|[?&](s|q|search|replytocom|print|share|action|ical"
    r"|month|year|day|date|tribe-bar-date|eventDisplay)=",
    re.I,
)


def normalize(url: str) -> str | None:
    """Canonical form used for fetching: no fragment, no tracking params, sorted query."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.hostname.lower()
    if parts.port and parts.port not in (80, 443):
        netloc += f":{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAM.match(k)
    ))
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def url_key(url: str) -> str:
    """Dedup key: ignores scheme, `www.` and trailing slash."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    key = f"{bare_host(parts.netloc)}{path}"
    return f"{key}?{parts.query}" if parts.query else key


def bare_host(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def extension(url: str) -> str:
    return posixpath.splitext(unquote(urlsplit(url).path))[1].lower()


def is_document_url(url: str) -> bool:
    return extension(url) in DOC_EXTENSIONS


def is_document_type(content_type: str) -> bool:
    return content_type.startswith(DOC_CONTENT_TYPES)


def is_external_doc_host(url: str) -> bool:
    host = bare_host(urlsplit(url).hostname or "")
    return any(host == h or host.endswith("." + h) for h in EXTERNAL_DOC_HOSTS)


def is_skipped(url: str) -> bool:
    return extension(url) in SKIP_EXTENSIONS or bool(TRAP.search(url))
