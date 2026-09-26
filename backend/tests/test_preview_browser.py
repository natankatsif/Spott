"""The preview's own script in a real browser, without the API or a database (docs/tasks/11 §Tests 3, the parts
that don't need the corpus): the three match tiers, the "page changed" banner, re-highlighting over postMessage
without a reload, the text view, and the full-screen back link. Views are rendered in-process (app/preview.py),
served from one local port and embedded from another, like the chat does.

    uv run pytest -m browser backend/tests/test_preview_browser.py      (needs `playwright install chromium`)
"""

import http.server
import json
import threading

import pytest

from app import preview

pytestmark = pytest.mark.browser

PREVIEW_PORT, PARENT_PORT = 8021, 8022
PREVIEW = f"http://127.0.0.1:{PREVIEW_PORT}"
PARENT = f"http://127.0.0.1:{PARENT_PORT}"
DOC = {"doc_id": "page:dgaurf.md/ro/anunt", "kind": "page", "url": "https://dgaurf.md/ro/anunt", "title": "Anunț",
       "indexed_at": "2026-09-25T10:00:00+00:00", "page_sizes": []}
FILLER = "".join(f"<p>Paragraful {i} fără legătură cu citatul, doar ca pagina să fie lungă.</p>" for i in range(60))
PAGE = f"""<!doctype html><html><head><title>Anunț</title></head><body><h1>Anunț</h1>{FILLER}
<p id="exact"><span>Direcţia generală</span> arhitectură, urbanism şi relaţii funciare va selecta compania prin
<b>procedura</b> de achiziţii publice.</p>{FILLER}
<p id="words">Termenul de depunere a ofertelor este de treizeci de zile lucrătoare de la data publicării anunțului.
</p>{FILLER}<p id="start">Contractul se semnează cu câștigătorul după expirarea termenului de contestare și după
aprobarea în ședință.</p></body></html>"""
LINES = [
    {"line_id": "exact", "text": "Direcția generală arhitectură, urbanism și relații funciare va selecta compania prin "
                                  "procedura de achiziții publice.", "page": None, "bboxes": []},
    # the index has another start, 8+ consecutive words are on the page
    {"line_id": "words", "text": "Conform regulamentului, termenul de depunere a ofertelor este de treizeci de zile "
                                  "lucrătoare de la data publicării.", "page": None, "bboxes": []},
    # the start is on the page, the end changed: still the "words" tier (a 12-word head is also an 8-word run, so
    # preview.js's "start" tier can't win over "words"; it is kept for the contract, the UI may treat both alike)
    {"line_id": "start", "text": "Contractul se semnează cu câștigătorul după expirarea termenului de contestare și "
                                  "după o nouă verificare a documentelor de către comisie.", "page": None, "bboxes": []},
    {"line_id": "gone", "text": "Acest paragraf a fost șters de pe pagină după indexare și nu mai apare nicăieri.",
     "page": None, "bboxes": []},
]
FILES: dict[str, str] = {}
PARENT_HTML = """<!doctype html><body style="margin:0"><iframe id="pv" style="width:1000px;height:600px;border:0"
sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-presentation">
</iframe><script>
window.__msgs = [];
addEventListener("message", (e) => { if (e.data && e.data.type === "src-preview:ready") window.__msgs.push(e.data); });
const q = new URLSearchParams(location.search);
document.getElementById("pv").src = q.get("src");
window.highlight = (ids) => document.getElementById("pv").contentWindow.postMessage(
  {type: "src-preview:highlight", line_ids: ids}, "*");
</script></body>"""

IN_VIEW = """() => {
  const h = window.CSS && CSS.highlights && CSS.highlights.get("src-quote");
  const el = h && h.size ? [...h][0] : document.querySelector("mark[data-src-quote], .src-line.src-on");
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.top >= 0 && r.bottom <= innerHeight;
}"""


def serve(port: int, pages: dict[str, str]) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = pages.get(self.path.split("?")[0])
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "").encode("utf-8"))

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    for name, selected in {"exact": ["exact"], "words": ["words"], "start": ["start"], "gone": ["gone"]}.items():
        FILES[f"/{name}.html"] = preview.page_view(DOC, LINES, selected, "ro", True, [PARENT], PAGE, "crawl",
                                                   "2026-09-25", "https://dgaurf.md/ro/anunt").body
    FILES["/text.html"] = preview.text_view(DOC | {"kind": "file"}, LINES, ["start"], "ru", False, [PARENT], "u").body
    servers = [serve(PREVIEW_PORT, FILES), serve(PARENT_PORT, {"/": PARENT_HTML})]
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # no browser downloaded
            pytest.skip(f"no Chromium: {e}")
        yield b
        b.close()
    for s in servers:
        s.shutdown()


def embed(browser, name: str):
    page = browser.new_page(viewport={"width": 1000, "height": 600})
    page.goto(f"{PARENT}/?src={PREVIEW}/{name}.html")
    page.wait_for_function("() => window.__msgs.length > 0", timeout=15_000)
    frame = next(f for f in page.frames if f.url.startswith(PREVIEW))
    return page, frame


@pytest.mark.parametrize("name,tier", [("exact", "exact"), ("words", "words"), ("start", "words")])
def test_quote_is_found_highlighted_and_in_view(browser, name, tier):
    page, frame = embed(browser, name)
    assert page.evaluate("() => window.__msgs[0]") == {"type": "src-preview:ready", "doc_id": DOC["doc_id"],
                                                       "found": tier, "kind": "page"}
    assert frame.evaluate(IN_VIEW) is True
    assert frame.evaluate("() => document.querySelector('#src-preview-banner .src-note').hidden")
    page.close()


def test_changed_page_says_so_and_shows_the_quote(browser):
    page, frame = embed(browser, "gone")
    assert page.evaluate("() => window.__msgs[0].found") == "none"
    note = frame.evaluate("() => { const n = document.querySelector('#src-preview-banner .src-note');"
                          " return n.hidden ? null : n.textContent; }")
    assert note and "pagina s-a schimbat" in note and "a fost șters" in note
    page.close()


def test_switching_lines_rehighlights_without_reload(browser):
    page, frame = embed(browser, "exact")
    frame.evaluate("() => { window.__kept = true; }")
    top = frame.evaluate("() => scrollY")
    page.evaluate(f"() => window.highlight({json.dumps(['start'])})")
    page.wait_for_function("() => window.__msgs.length > 1", timeout=10_000)
    assert page.evaluate("() => window.__msgs[1].found") == "words"
    assert frame.evaluate("() => window.__kept") is True, "the preview reloaded"
    assert frame.evaluate("() => scrollY") > top and frame.evaluate(IN_VIEW) is True
    page.close()


def test_messages_from_other_origins_are_ignored(browser):
    page = browser.new_page()
    page.goto(f"{PREVIEW}/exact.html")  # opened directly: the page itself is not an allowed origin
    page.wait_for_function("() => document.documentElement.dataset.srcFound", timeout=10_000)
    page.evaluate("() => { window.postMessage({type: 'src-preview:highlight', line_ids: ['start']}, '*'); }")
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.documentElement.dataset.srcFound") == "exact"
    page.close()


def test_mobile_text_view_full_screen(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.goto(f"{PREVIEW}/text.html")
    page.wait_for_function("() => document.documentElement.dataset.srcFound", timeout=10_000)
    assert page.evaluate("() => document.documentElement.dataset.srcFound") == "exact"
    assert page.query_selector("a[data-src-back]") is not None and "Назад" in page.inner_text("a[data-src-back]")
    assert page.evaluate(IN_VIEW) is True
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), "horizontal scroll on a phone"
    context.close()
