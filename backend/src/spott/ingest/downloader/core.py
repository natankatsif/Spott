"""Downloads discovered documents into content-addressed storage: data/raw/<sha[:2]>/<sha><ext>.

Three dedup levels, cheapest first:
    1. URL          only documents not downloaded yet are fetched (unless --refresh / --retry-failed)
    2. HTTP cache   on --refresh, If-None-Match / If-Modified-Since; a 304 skips the body
    3. SHA-256      identical content from another URL is stored (and later parsed) once
"""

import asyncio
import hashlib
import logging
import mimetypes
import re
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

from spott.ingest.common.http import HTML_TYPES, RETRY_STATUSES, content_type, tls_failed
from spott.ingest.common.progress import Progress
from spott.ingest.common.registry import Registry
from spott.ingest.common.urls import DOC_EXTENSIONS, bare_host

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 200 * 1024 * 1024
RETRIES = 2

GDRIVE_FILE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?.*\bid=)([\w-]+)")
GDOCS = re.compile(r"docs\.google\.com/(document|spreadsheets|presentation)/d/([\w-]+)")
GDOCS_EXPORT = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pdf"}
FILENAME = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", re.I)


class RetryableStatus(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class FileTooLarge(Exception):
    pass


def download_url(url: str) -> str:
    """Google Drive / Docs viewer links → direct download links."""
    if m := GDRIVE_FILE.search(url):
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    if m := GDOCS.search(url):
        kind, doc_id = m.groups()
        return f"https://docs.google.com/{kind}/d/{doc_id}/export?format={GDOCS_EXPORT[kind]}"
    return url


def file_extension(url_extension: str, resp: httpx.Response, ctype: str) -> str:
    if url_extension in DOC_EXTENSIONS:
        return url_extension
    m = FILENAME.search(resp.headers.get("content-disposition", ""))
    if m and (suffix := PurePosixPath(unquote(m.group(1))).suffix.lower()):
        return suffix
    return mimetypes.guess_extension(ctype) or ""


class Downloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        registry: Registry,
        data_dir: Path,
        *,
        delay: float,
        refresh: bool,
    ):
        self.client = client
        self.insecure_client = insecure_client
        self.registry = registry
        self.data_dir = data_dir
        self.tmp_dir = data_dir / "raw" / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.refresh = refresh
        self.insecure_hosts: set[str] = set()
        self.stats: Counter[str] = Counter()
        self.progress = Progress()

    async def download_host(self, host: str, docs: list[dict]) -> None:
        """Documents of one host, sequentially with a pause — same politeness as the crawler."""
        for i, doc in enumerate(docs, 1):
            if self.progress.cancelled():
                return
            outcome = await self.download(doc)
            self.stats[outcome] += 1
            self.progress.advance(error=outcome == "failed")
            log.info("%s [%d/%d] %s %s", host, i, len(docs), outcome, doc["url"])

    async def download(self, doc: dict) -> str:
        url = download_url(doc["url"])
        host = bare_host(urlsplit(url).hostname or "")
        headers = {}
        if self.refresh and doc["status"] == "downloaded":
            if doc["etag"]:
                headers["If-None-Match"] = doc["etag"]
            if doc["last_modified"]:
                headers["If-Modified-Since"] = doc["last_modified"]

        attempt = 0
        while True:
            client = self.insecure_client if host in self.insecure_hosts else self.client
            try:
                return await self._request(client, doc, url, headers)
            except httpx.TransportError as e:
                if tls_failed(e) and host not in self.insecure_hosts:
                    log.warning("%s: TLS certificate invalid, continuing without verification", host)
                    self.insecure_hosts.add(host)
                    continue
                if attempt >= RETRIES:
                    self.registry.mark_checked(doc["key"], "failed", error=f"{type(e).__name__}: {e}")
                    return "failed"
            except RetryableStatus as e:
                if attempt >= RETRIES:
                    self.registry.mark_checked(doc["key"], "failed", http_status=e.status)
                    return "failed"
            except (httpx.HTTPError, FileTooLarge) as e:
                self.registry.mark_checked(doc["key"], "failed", error=f"{type(e).__name__}: {e}")
                return "failed"
            attempt += 1
            await asyncio.sleep(2**attempt)

    async def _request(self, client: httpx.AsyncClient, doc: dict, url: str, headers: dict) -> str:
        key = doc["key"]
        tmp = self.tmp_dir / f"{uuid.uuid4().hex}.part"
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                status = resp.status_code
                if status == 304:
                    self.registry.mark_not_modified(key)
                    return "not_modified"
                if status in RETRY_STATUSES:
                    raise RetryableStatus(status)
                if status in (404, 410):
                    is_removed = self.registry.record_download_missing(key, status)
                    return "removed" if is_removed else "missing"
                if status >= 400:
                    self.registry.mark_checked(key, "failed", http_status=status)
                    return "failed"

                ctype = content_type(resp)
                if ctype in HTML_TYPES:
                    # A page, not a file: legis.md acts, Drive folders, dead links redirecting home.
                    self.registry.mark_checked(key, "not_a_file", http_status=status)
                    return "not_a_file"

                hasher, size = hashlib.sha256(), 0
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise FileTooLarge(f"larger than {MAX_FILE_BYTES // 2**20} MB")
                        hasher.update(chunk)
                        f.write(chunk)
                ext = file_extension(doc["extension"], resp, ctype)
                etag, last_modified = resp.headers.get("etag"), resp.headers.get("last-modified")

            if size == 0:
                self.registry.mark_checked(key, "failed", http_status=status, error="empty response")
                return "failed"

            sha = hasher.hexdigest()
            is_new_file = not self.registry.has_file(sha)
            path = None
            if is_new_file:
                rel = Path("raw") / sha[:2] / f"{sha}{ext}"
                (self.data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                tmp.replace(self.data_dir / rel)
                path = rel.as_posix()
            self.registry.record_download(
                key, sha256=sha, path=path, size=size, content_type=ctype, extension=ext,
                http_status=status, etag=etag, last_modified=last_modified,
            )
            if doc["sha256"] == sha:
                return "unchanged"
            if doc["sha256"]:
                return "updated"  # new content at a known URL; the old version stays in document_versions
            return "new_file" if is_new_file else "duplicate"
        finally:
            tmp.unlink(missing_ok=True)
            await asyncio.sleep(self.delay)
