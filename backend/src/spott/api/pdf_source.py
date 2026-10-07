"""PDF bytes for the source viewer (GET /api/documents/{doc_id}/file).

City hall sites send no CORS headers, so the browser's PDF viewer can't load their files directly.
The backend fetches the document from its original URL on request and passes it through, keeping
a small in-memory cache; nothing is stored on disk. A stored copy (data/raw) is used
when this machine has one. Only documents in our index can be fetched: it isn't an open proxy.
"""

import logging
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx
from starlette.concurrency import run_in_threadpool

from .errors import ApiException
from .files import raw_pdf

log = logging.getLogger("backend.pdf")

MAX_PDF_BYTES = 60 * 1024 * 1024
CACHE_BYTES = 200 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; ChisinauAssistant/0.1; +GigaHack 2026)"


def is_pdf_url(url: str | None) -> bool:
    return bool(url) and urlsplit(url).path.lower().endswith(".pdf")


def make_clients() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """(client, insecure_client): some .md sites have broken TLS certificates; the content is public."""
    opts = {"headers": {"User-Agent": USER_AGENT}, "follow_redirects": True,
            "timeout": httpx.Timeout(30.0, connect=10.0)}
    return httpx.AsyncClient(**opts), httpx.AsyncClient(verify=False, **opts)


class PdfSource:
    def __init__(self, client: httpx.AsyncClient, insecure_client: httpx.AsyncClient,
                 cache_bytes: int = CACHE_BYTES):
        self.client, self.insecure_client = client, insecure_client
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.cache_bytes = cache_bytes

    async def get(self, doc: dict) -> bytes:
        """doc: a row of the documents table (doc_id, kind, url, sha256)."""
        doc_id = doc["doc_id"]
        if local := raw_pdf(doc_id, doc.get("sha256")):
            return await run_in_threadpool(local.read_bytes)
        if doc_id in self.cache:
            self.cache.move_to_end(doc_id)
            return self.cache[doc_id]
        if doc.get("kind") != "file" or not is_pdf_url(doc.get("url")):
            raise ApiException(404, "not_found", f"{doc_id} is not a PDF")
        data = await self.fetch(doc["url"])
        self.remember(doc_id, data)
        return data

    async def fetch(self, url: str) -> bytes:
        try:
            try:
                return await self.download(self.client, url)
            except httpx.ConnectError as e:
                if "CERTIFICATE_VERIFY_FAILED" not in str(e):
                    raise
                return await self.download(self.insecure_client, url)
        except httpx.HTTPError as e:
            log.warning("PDF fetch failed %s: %s", url, e)
            raise ApiException(503, "unavailable", "The city hall site didn't return the document") from e

    @staticmethod
    async def download(client: httpx.AsyncClient, url: str) -> bytes:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise ApiException(404, "not_found", f"The city hall site answered {resp.status_code}")
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ApiException(404, "not_found", "Document too large for the viewer")
                chunks.append(chunk)
        data = b"".join(chunks)
        if not data.startswith(b"%PDF"):
            raise ApiException(404, "not_found", "The link doesn't return a PDF")
        return data

    def remember(self, doc_id: str, data: bytes) -> None:
        self.cache[doc_id] = data
        while sum(len(v) for v in self.cache.values()) > self.cache_bytes and len(self.cache) > 1:
            self.cache.popitem(last=False)
