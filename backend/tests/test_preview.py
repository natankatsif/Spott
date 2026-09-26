"""Source preview without a browser or a database (docs/tasks/11 §Tests 1): the sanitizer, the "is the quote on the
rendered page" check, the three views, the headers, and GET /api/preview/{doc_id} with a fake store.
The in-browser matching and highlighting is covered by test_preview_e2e.py (marker `browser`)."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main, preview

QUOTE = "Direcția generală arhitectură va selecta compania prin procedura de achiziții publice"
PAGE = f"""<!doctype html><html><head><title>Anunț</title><base href="https://evil.example/">
<meta http-equiv="refresh" content="0;url=https://evil.example/"><script>alert(1)</script>
<link rel="stylesheet" href="/css/site.css"></head>
<body onload="steal()"><h1>Anunț</h1><p>Text înainte.</p><p><span>Direcția generală</span> arhitectură va
selecta compania <b>prin procedura</b> de achiziții publice</p>
<a href="javascript:alert(2)">rău</a><a href="/ro/alt">bun</a><img src="/img/a.png" onerror="x()">
<iframe src="https://evil.example/"></iframe><form action="https://evil.example/post"><input></form>
<noscript>fără js</noscript></body></html>"""
DOC_PAGE = {"doc_id": "page:dgaurf.md/ro/anunt", "kind": "page", "url": "https://dgaurf.md/ro/anunt", "title": "Anunț",
            "site": "dgaurf.md", "page_sizes": [], "indexed_at": "2026-09-25T10:00:00+00:00", "has_file": False}
DOC_PDF = DOC_PAGE | {"doc_id": "file:dgaurf.md/storage/d79.pdf", "kind": "file", "url": "https://dgaurf.md/storage/d79.pdf",
                      "page_sizes": [{"n": 1, "width": 595.8, "height": 842.4}], "has_file": True}
DOC_DOCX = DOC_PAGE | {"doc_id": "file:dgaurf.md/storage/regulament.docx", "kind": "file",
                       "url": "https://dgaurf.md/storage/regulament.docx"}


def line(i: int, text: str, page: int | None = None, bboxes: list | None = None) -> dict:
    return {"line_id": f"l{i}", "text": text, "page": page, "bboxes": bboxes or []}


LINES = [line(0, "Anunț"), line(1, QUOTE), line(2, "Termenul este de 30 de zile.")]
BOX = {"page": 1, "l": 160.0, "t": 210.0, "r": 850.0, "b": 270.0, "origin": "BOTTOMLEFT"}


# ─────────────── sanitizer ───────────────


def test_sanitize_drops_everything_that_runs():
    clean = preview.sanitize(PAGE)
    low = clean.lower()
    assert "<script" not in low and "<iframe" not in low and "<noscript" not in low
    assert "http-equiv" not in low and "evil.example/\"" not in low.split("<body")[0]  # the site's own <base> is gone
    assert not re.search(r"\son[a-z]+=", low), "an event handler survived"
    assert "javascript:" not in low
    assert "https://evil.example/post" not in clean  # form targets
    assert 'href="/css/site.css"' in clean and 'src="/img/a.png"' in clean  # layout stays
    for a in re.findall(r"<a [^>]*>", clean):
        assert 'target="_blank"' in a and "noopener" in a


def test_page_view_adds_only_our_script_with_the_nonce_and_a_base():
    view = preview.page_view(DOC_PAGE, LINES, ["l1"], "ro", True, ["http://localhost:3000"], PAGE, "crawl",
                             "2026-09-25T10:00:00+00:00", "https://dgaurf.md/ro/anunt#:~:text=x")
    assert view.kind == "page"
    scripts = re.findall(r"<script\b[^>]*>", view.body)
    runnable = [s for s in scripts if 'type="application/json"' not in s]
    assert runnable == [f'<script nonce="{view.nonce}">'], runnable
    assert '<base href="https://dgaurf.md/ro/anunt">' in view.body
    assert "Copie din 25.09.2026" in view.body and "Deschide originalul" in view.body
    assert "<a href=\"#\" data-src-back>" not in view.body  # embed=1: no back link
    data = json.loads(re.search(r'id="src-preview-data">(.*?)</script>', view.body, re.S).group(1))
    assert data["selected"] == ["l1"] and data["lines"]["l1"]["text"] == QUOTE
    assert data["allowed_origins"] == ["http://localhost:3000"]


def test_data_block_cannot_close_its_script():
    view = preview.text_view(DOC_DOCX, [line(0, "</script><script>alert(1)</script>")], ["l0"], "ro", True, [], "u")
    block = re.search(r'id="src-preview-data">(.*?)</script>', view.body, re.S).group(1)
    assert "</script" not in block and json.loads(block)["lines"]["l0"]["text"].startswith("</script>")


# ─────────────── is the quote on the rendered page ───────────────


def test_shown_text_matches_across_tags_and_diacritics():
    assert preview.shown_text_has(PAGE, QUOTE) is True
    cedilla = PAGE.replace("Direcția", "Direcţia")  # ţ (cedilla) on the page, ț (comma) in the index
    assert preview.shown_text_has(cedilla, QUOTE) is True


def test_shown_text_only_in_script_data_means_a_js_page():
    js_page = f"<html><body><div id=app></div><script>window.__DATA__={{t:'{QUOTE}'}}</script></body></html>"
    assert preview.shown_text_has(js_page, QUOTE) is False
    assert preview.shown_text_has("<html><body><p>alt text</p></body></html>", QUOTE) is None  # page changed


def test_js_built_page_falls_back_to_the_text_view():
    js_page = f"<html><body><div id=app></div><template>{QUOTE}</template></body></html>"
    view = preview.page_view(DOC_PAGE, LINES, ["l1"], "ro", True, [], js_page, "live", "2026-09-26", "u")
    assert view.kind == "text" and "scripturi" in view.body


# ─────────────── PDF and text views ───────────────


def test_pdf_view_embeds_the_page_boxes_and_the_viewer():
    lines = [line(0, "Titlu", 1), line(1, QUOTE, 2, [BOX])]
    view = preview.pdf_view(DOC_PDF, lines, ["l1"], "ru", False, ["*"], "/api/documents/x/file", "u#page=2")
    data = json.loads(re.search(r'id="src-preview-data">(.*?)</script>', view.body, re.S).group(1))
    assert view.kind == "pdf" and data["first_page"] == 2 and data["file_url"] == "/api/documents/x/file"
    assert data["lines"]["l1"]["bboxes"] == [BOX] and data["page_sizes"] == DOC_PDF["page_sizes"]
    assert f'<script type="module" nonce="{view.nonce}" src="/api/preview-static/pdf-viewer.js">' in view.body
    assert "стр. 2" in view.body and "Открыть оригинал PDF" in view.body
    assert "<a href=\"#\" data-src-back>" in view.body  # embed=0 (full screen on a phone): a way back


def test_text_view_highlights_the_lines_and_links_the_original():
    view = preview.text_view(DOC_DOCX, LINES, ["l1", "l2"], "ro", True, [], "u")
    assert view.kind == "text"
    assert view.body.count('class="src-line src-on"') == 2
    assert 'href="https://dgaurf.md/storage/regulament.docx"' in view.body and "Descarcă originalul" in view.body


def test_vendored_pdfjs_is_the_legacy_build():
    """The modern build needs Map.getOrInsertComputed (Chrome ~145+); the legacy one polyfills it."""
    pdf = (preview.STATIC / "pdfjs" / "pdf.min.mjs").read_text(encoding="utf-8")
    assert "getOrInsertComputed:function" in pdf


# ─────────────── the endpoint ───────────────


class FakeStore:
    docs = {d["doc_id"]: d for d in (DOC_PAGE, DOC_PDF, DOC_DOCX)}

    def preview_document(self, doc_id):
        return self.docs.get(doc_id)

    def doc_lines(self, doc_id):
        return [{"line_id": ln["line_id"], "text": ln["text"], "page": 1, "bboxes": [], "chunk_bboxes": [],
                 "pages": [1]} for ln in LINES]


class FakePages:
    def __init__(self, found):
        self.found = found

    async def get(self, url):
        return self.found


@pytest.fixture
def client():
    main.app.state.store = FakeStore()
    main.app.state.pages = FakePages((PAGE, "crawl", "2026-09-25T10:00:00+00:00"))
    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.state.store = None


def get(client, doc_id, **params):
    from urllib.parse import quote

    return client.get(f"/api/preview/{quote(doc_id, safe='')}", params=params)


def test_unknown_document_is_404_api_error(client):
    r = get(client, "page:nope.md/x")
    assert (r.status_code, r.json()["error"]) == (404, "not_found")


@pytest.mark.parametrize("doc,kind", [(DOC_PAGE, "page"), (DOC_PDF, "pdf"), (DOC_DOCX, "text")])
def test_headers_allow_our_frontend_to_frame_it(client, doc, kind):
    r = get(client, doc["doc_id"], line="l1", lang="ro")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'nonce="{nonce}"' in r.text
    assert "frame-ancestors 'self' " + " ".join(main.CORS_ORIGINS) in csp
    assert "x-frame-options" not in r.headers
    assert r.headers["cache-control"] == "private, max-age=600"
    if kind == "pdf":
        assert "worker-src 'self' blob:" in csp and "'wasm-unsafe-eval'" in csp
    else:
        assert "wasm" not in csp and "worker-src" not in csp


def test_only_known_lines_up_to_five_are_selected(client):
    r = get(client, DOC_DOCX["doc_id"], line=["zzz", "l2", "l1", "l2"])
    data = json.loads(re.search(r'id="src-preview-data">(.*?)</script>', r.text, re.S).group(1))
    assert data["selected"] == ["l2", "l1"]


def test_unreachable_page_is_our_text_view_not_an_empty_frame(client):
    main.app.state.pages = FakePages(None)
    r = get(client, DOC_PAGE["doc_id"], line="l1", lang="ru")
    assert r.status_code == 200 and "Оригинальную страницу сейчас не удалось загрузить" in r.text
    assert 'class="src-line src-on"' in r.text


def test_preview_url_and_kind_helpers():
    assert preview.preview_url("page:a.md/x y", ["l1", "l2"], "ru") == "/api/preview/page%3Aa.md%2Fx%20y?lang=ru&line=l1&line=l2"
    assert preview.preview_url("d", [f"l{i}" for i in range(9)], "ro").count("&line=") == preview.MAX_LINES
    assert preview.preview_kind("page", "https://a.md/x.pdf") == "page"
    assert preview.preview_kind("file", "https://a.md/x.pdf") == "pdf"
    assert preview.preview_kind("file", "https://a.md/x.docx") == "text"
    assert preview.preview_kind("file", "https://a.md/download?id=3", has_file=True) == "pdf"
