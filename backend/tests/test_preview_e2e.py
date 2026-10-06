"""Source preview in a real browser (docs/tasks/11 §Tests 3-4): headless Chromium, real corpus documents.

Needs the index in Postgres and a running API with the harness origin allowed:
    CORS_ORIGINS=http://127.0.0.1:8012 uv run uvicorn app.main:app --port 8011      (in backend/)
    PREVIEW_API=http://127.0.0.1:8011 uv run pytest -m browser backend/tests/test_preview_e2e.py
The chat is mimicked by preview_harness/index.html, served from another origin (port 8012), embedding the preview
in an iframe with the same attributes as AI Elements' WebPreviewBody. Screenshots and timings go to
backend/tests/artifacts/preview/ (gitignored). No LLM is called.
"""

import functools
import http.server
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

pytestmark = [pytest.mark.db, pytest.mark.browser]

API = os.getenv("PREVIEW_API", "http://127.0.0.1:8011")
HARNESS_PORT = 8012
ARTIFACTS = Path(__file__).parent / "artifacts" / "preview"
# Real documents of the index: 3 web pages, 2 text PDFs, 1 scan (every dgaurf.md PDF is one), 1 page whose live
# version differs from the index.
CASES = {
    "dgaurf-page": "page:dgaurf.md/ro/services",
    "proiecte-page": "page:proiecte.chisinau.md/ro/n-8-buiucani",
    "help-page": "page:help.chisinau.md/ru/chasto-zadavaemye-voprosy",
    "text-pdf-dgmu-etica": "file:e6a5c0787d264b9182304b66dbbcfff3cd90a4e10ad33a0a2ca4dc04ac297024",
    "text-pdf-autosalubritate": "file:5ed0c919e9bb8f7b568be747f9479338fe59920c3a807f31a265f40cf865c782",
    "scanned-pdf": "file:eaea1f34bba54ab166be541369763d03717b66dd696b92077362ca2b3bde05ae",  # decizia 12/14, a scan
    "changed-page": os.getenv("PREVIEW_CHANGED_DOC", ""),
}
RESULTS: dict[str, dict] = {}

VIEW_RECT = """() => {
  const pick = () => {
    const h = window.CSS && CSS.highlights && CSS.highlights.get("src-quote");
    if (h && h.size) return [...h][0].getBoundingClientRect();
    const el = document.querySelector(".src-box, .textLayer .src-on, mark[data-src-quote], .src-line.src-on");
    return el && el.getBoundingClientRect();
  };
  const r = pick();
  return r ? {top: r.top, bottom: r.bottom, height: innerHeight, kind: document.documentElement.dataset.srcFound}
           : null;
}"""


def two_lines(doc_id: str) -> list[str]:
    """A long line near the start and one further on (the switch)."""
    from retrieval.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT l.line_id FROM lines l JOIN chunks c USING (chunk_id) WHERE l.doc_id = %s AND length(l.text) > 60 "
            "ORDER BY (c.block_ids->>0)::int NULLS LAST, l.idx", (doc_id,)).fetchall()
    ids = [r[0] for r in rows]
    if len(ids) < 2:
        pytest.skip(f"{doc_id}: not enough lines")
    return [ids[min(2, len(ids) - 2)], ids[len(ids) * 2 // 3]]


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        if httpx.get(f"{API}/health", timeout=3).json().get("models_loaded") is not True:
            pytest.skip("API not ready")
    except httpx.HTTPError:
        pytest.skip(f"no API at {API}")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(Path(__file__).parent / "preview_harness"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", HARNESS_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()
    server.shutdown()
    (ARTIFACTS / "results.json").write_text(json.dumps(RESULTS, indent=1), encoding="utf-8")


def src(doc_id: str, line_id: str, lang: str = "ro", embed: int = 1) -> str:
    return f"/api/preview/{quote(doc_id, safe='')}?line={quote(line_id, safe='')}&lang={lang}&embed={embed}"


def frame_errors(messages: list[str]) -> list[str]:
    return [m for m in messages if any(x in m for x in ("frame-ancestors", "X-Frame-Options", "Refused to execute",
                                                        "Refused to load the script", "Refused to create a worker"))]


@pytest.mark.parametrize("name", list(CASES))
def test_inline_preview(browser, name):
    doc_id = CASES[name]
    if not doc_id:
        pytest.skip(f"set PREVIEW_{name.split('-')[0].upper()}_DOC")
    first, second = two_lines(doc_id)
    lang = "ru" if "/ru/" in doc_id else "ro"
    page = browser.new_page(viewport={"width": 1100, "height": 700})
    console: list[str] = []
    page.on("console", lambda m: console.append(m.text) if m.type in ("error", "warning") else None)
    page.goto(f"http://127.0.0.1:{HARNESS_PORT}/index.html?api={quote(API)}&src={quote(src(doc_id, first, lang))}")
    page.wait_for_function("() => window.__msgs.length > 0", timeout=45_000)
    ready = page.evaluate("() => ({...window.__msgs[0], ms: window.__msgs[0].t - window.__start})")
    frame = next(f for f in page.frames if f.url.startswith(API + "/api/preview/"))
    rect = frame.evaluate(VIEW_RECT)
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"))
    RESULTS[name] = {"doc_id": doc_id, "kind": ready["kind"], "found": ready["found"], "ready_ms": round(ready["ms"]),
                     "html_bytes": len(httpx.get(API + src(doc_id, first, lang), timeout=30).content)}

    assert not frame_errors(console), console
    if name == "changed-page":
        assert ready["found"] == "none"  # the banner says the page changed and shows the quote
        assert frame.evaluate("() => !document.querySelector('#src-preview-banner .src-note').hidden")
        return
    assert ready["found"] != "none"
    assert rect and 0 <= rect["top"] < rect["height"] and rect["bottom"] > 0, rect

    # Another line of the same document: re-highlighted and scrolled without reloading.
    frame.evaluate("() => { window.__kept = true; }")
    page.evaluate(f"() => window.highlight([{json.dumps(second)}])")
    page.wait_for_function("() => window.__msgs.length > 1", timeout=20_000)
    again = page.evaluate("() => window.__msgs[1]")
    assert frame.evaluate("() => window.__kept === true"), "the preview reloaded"
    RESULTS[name]["switch_found"] = again["found"]
    if again["found"] != "none":
        rect = frame.evaluate(VIEW_RECT)
        assert rect and 0 <= rect["top"] < rect["height"], rect
    page.close()


@pytest.mark.parametrize("name", ["proiecte-page", "text-pdf-dgmu-etica"])
def test_mobile_full_screen(browser, name):
    doc_id = CASES[name]
    first, _ = two_lines(doc_id)
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = context.new_page()
    started = time.monotonic()
    page.goto(API + src(doc_id, first, embed=0))
    page.wait_for_function("() => document.documentElement.dataset.srcFound", timeout=45_000)
    found = page.evaluate("() => document.documentElement.dataset.srcFound")
    assert page.query_selector("[data-src-back]") is not None  # full screen: a way back to the chat
    assert found != "none"
    rect = page.evaluate(VIEW_RECT)
    assert rect and 0 <= rect["top"] < rect["height"], rect
    page.screenshot(path=str(ARTIFACTS / f"mobile-{name}.png"))
    RESULTS[f"mobile-{name}"] = {"doc_id": doc_id, "found": found, "ready_ms": round((time.monotonic() - started) * 1000)}
    context.close()
