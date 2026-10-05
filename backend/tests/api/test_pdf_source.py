"""PDF pass-through for the source viewer, with a fake city hall site (no network)."""

import asyncio

import httpx
import pytest

from spott.api.errors import ApiException
from spott.api.pdf_source import PdfSource

PDF = b"%PDF-1.7 fake"
DOC = {"doc_id": "file:dgaurf.md/storage/d.pdf", "kind": "file", "url": "https://dgaurf.md/storage/d.pdf",
       "sha256": None}


def source(handler) -> tuple[PdfSource, list[str]]:
    calls = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    return PdfSource(client, client), calls


def test_fetches_by_url_and_caches():
    pdfs, calls = source(lambda r: httpx.Response(200, content=PDF))
    assert asyncio.run(pdfs.get(DOC)) == PDF
    assert asyncio.run(pdfs.get(DOC)) == PDF
    assert calls == ["https://dgaurf.md/storage/d.pdf"]


def test_web_pages_and_non_pdf_links_are_not_fetched():
    pdfs, calls = source(lambda r: httpx.Response(200, content=PDF))
    for doc in (DOC | {"kind": "page"}, DOC | {"url": "https://dgaurf.md/storage/d.docx"}):
        with pytest.raises(ApiException) as e:
            asyncio.run(pdfs.get(doc))
        assert e.value.code == "not_found"
    assert calls == []


def test_html_instead_of_pdf_is_rejected():
    pdfs, _ = source(lambda r: httpx.Response(200, content=b"<html>not found page</html>"))
    with pytest.raises(ApiException) as e:
        asyncio.run(pdfs.get(DOC))
    assert e.value.code == "not_found"


def test_site_down_is_unavailable():
    def down(request):
        raise httpx.ConnectTimeout("timeout")

    pdfs, _ = source(down)
    with pytest.raises(ApiException) as e:
        asyncio.run(pdfs.get(DOC))
    assert (e.value.status, e.value.code) == (503, "unavailable")


def test_cache_evicts_oldest_over_the_limit():
    pdfs, _ = source(lambda r: httpx.Response(200, content=PDF))
    pdfs.cache_bytes = len(PDF) * 2
    for i in range(3):
        asyncio.run(pdfs.get(DOC | {"doc_id": f"file:d{i}.pdf"}))
    assert list(pdfs.cache) == ["file:d1.pdf", "file:d2.pdf"]
