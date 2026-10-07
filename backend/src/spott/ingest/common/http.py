"""HTTP settings shared by the crawler and the downloader."""

from contextlib import asynccontextmanager

import httpx

from spott.core.sources import USER_AGENT

HTML_TYPES = ("text/html", "application/xhtml+xml")
RETRY_STATUSES = {429, 500, 502, 503, 504}


def content_type(resp: httpx.Response) -> str:
    return resp.headers.get("content-type", "").split(";")[0].strip().lower()


def tls_failed(error: httpx.TransportError) -> bool:
    return isinstance(error, httpx.ConnectError) and "CERTIFICATE_VERIFY_FAILED" in str(error)


@asynccontextmanager
async def make_clients():
    """Yields (client, insecure_client). The insecure one is a fallback for sites with broken TLS certs."""
    opts = {
        "headers": {"User-Agent": USER_AGENT, "Accept-Language": "ro,ru;q=0.9,en;q=0.5"},
        "follow_redirects": True,
        "timeout": httpx.Timeout(30.0, connect=10.0),
    }
    async with httpx.AsyncClient(**opts) as client, httpx.AsyncClient(verify=False, **opts) as insecure:
        yield client, insecure
