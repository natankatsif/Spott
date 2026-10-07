"""Source preview (GET /api/preview/{doc_id}, docs/history/tasks/11): one URL that shows a cited source scrolled to the
quoted lines, highlighted, and that works inside the chat's iframe whatever the city hall site sends.

Text fragments (#:~:text=) don't work inside iframes, and city hall pages can't be framed (X-Frame-Options, CSP,
cross-origin). So the backend serves its own page:
- a web page: its crawled copy (else a live fetch, cached 1 h), sanitized (no site scripts, frames, forms,
  handlers), with <base href> to the original for CSS and images, and our script (static/preview.js) that finds the
  quote in the text and highlights it;
- a PDF: a pdf.js viewer (vendored in static/pdfjs) that renders the cited page first, draws the citation boxes and
  highlights the line in the text layer (static/pdf-viewer.js); the file comes from /api/documents/{doc_id}/file;
- DOCX, other files, and a page that can't be loaded: our own text view of the indexed lines.
Unknown doc_id → 404; everything else → 200 with the best view there is, never an empty frame.
"""

import asyncio
import html
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote as url_quote
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg_pool import ConnectionPool
from selectolax.parser import HTMLParser

from .files import DATA_DIR
from .pdf_source import is_pdf_url

log = logging.getLogger("backend.preview")

STATIC = Path(__file__).resolve().parent / "static"
STATIC_PREFIX = "/api/preview-static"
CRAWLER_AGENT = "ChisinauAssistantBot/0.1 (+GigaHack 2026; municipal RAG research crawler)"
LIVE_TIMEOUT_S = 5.0
LIVE_CACHE_S = 3600
MAX_LINES = 5
DOCUMENT_TYPES = ("page", "pdf", "text")

TEXT = {
    "ro": {"copy": "Copie din {date}", "live": "Copie de pe site, {date}", "open": "Deschide originalul ↗",
           "open_pdf": "Deschide PDF-ul original ↗", "download": "Descarcă originalul ↗", "page": "pagina {n}",
           "back": "← Înapoi", "saved_text": "Textul documentului, așa cum l-am indexat",
           "unavailable": "Pagina originală nu a putut fi încărcată acum; mai jos este textul ei, așa cum l-am indexat.",
           "scripted": "Această pagină își afișează conținutul prin scripturi; mai jos este textul ei, așa cum l-am indexat.",
           "not_found": "Fragmentul nu a fost găsit exact pe pagină — pagina s-a schimbat. Citatul: «{quote}»",
           "pdf_failed": "PDF-ul nu a putut fi încărcat acum. Deschideți originalul.", "loading": "Se încarcă PDF-ul…"},
    "ru": {"copy": "Копия от {date}", "live": "Копия с сайта, {date}", "open": "Открыть оригинал ↗",
           "open_pdf": "Открыть оригинал PDF ↗", "download": "Скачать оригинал ↗", "page": "стр. {n}",
           "back": "← Назад", "saved_text": "Текст документа в том виде, как мы его проиндексировали",
           "unavailable": "Оригинальную страницу сейчас не удалось загрузить; ниже её текст из нашего индекса.",
           "scripted": "Эта страница показывает содержимое скриптами; ниже её текст из нашего индекса.",
           "not_found": "Фрагмент не найден на странице точно — страница изменилась. Цитата: «{quote}»",
           "pdf_failed": "PDF сейчас не удалось загрузить. Откройте оригинал.", "loading": "Загрузка PDF…"},
    "en": {"copy": "Copy from {date}", "live": "Copy from the site, {date}", "open": "Open the original ↗",
           "open_pdf": "Open the original PDF ↗", "download": "Download the original ↗", "page": "page {n}",
           "back": "← Back", "saved_text": "The document's text, as we indexed it",
           "unavailable": "The original page couldn't be loaded now; below is its text as we indexed it.",
           "scripted": "This page shows its content with scripts; below is its text as we indexed it.",
           "not_found": "The passage wasn't found exactly on the page — the page has changed. The quote: «{quote}»",
           "pdf_failed": "The PDF couldn't be loaded now. Open the original.", "loading": "Loading the PDF…"},
}

STYLE = """
#src-preview-banner{all:initial;position:fixed;top:0;left:0;right:0;z-index:2147483647;display:flex;
flex-wrap:wrap;gap:2px 12px;align-items:center;padding:6px 12px;background:#111827;color:#f9fafb;
font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.35)}
#src-preview-banner *{font:inherit;color:inherit}
#src-preview-banner a{color:#93c5fd;text-decoration:underline;cursor:pointer}
#src-preview-banner .src-note{flex-basis:100%;color:#fde68a}
#src-preview-spacer{height:44px}
html{scroll-padding-top:56px}
::highlight(src-quote){background-color:#fde047;color:#111827}
mark[data-src-quote]{background:#fde047;color:#111827;padding:0}
"""
TEXT_VIEW_STYLE = """
body{margin:0;background:#f8fafc;color:#111827;font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:820px;margin:0 auto;padding:12px 20px 60px}
h1{font-size:18px;line-height:1.35;margin:8px 0 4px}
.src-sub{color:#6b7280;font-size:13px;margin-bottom:12px}
.src-line{margin:0 0 6px;padding:2px 6px;border-radius:4px;white-space:pre-wrap}
.src-line.src-on{background:#fde047}
@media (prefers-color-scheme: dark){body{background:#0f172a;color:#e5e7eb}.src-sub{color:#9ca3af}
.src-line.src-on{background:#a16207;color:#fff}}
"""
# The scrollbar is always there (pages are sized to the width left next to it, so no sideways scroll) and dark, so
# no light scrollbar tracks show at the side and bottom of the grey viewer.
PDF_STYLE = """
html{overflow-y:scroll;scrollbar-color:#8b8f94 #525659}
body{margin:0;background:#525659;overflow-x:hidden}
#pages{display:flex;flex-direction:column;align-items:center;gap:12px;padding:12px 8px 60px}
.page{position:relative;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.4)}
.page canvas{display:block}
.textLayer{position:absolute;inset:0;overflow:hidden;line-height:1;text-align:initial;opacity:1;z-index:2;
forced-color-adjust:none;transform-origin:0 0}
.textLayer span,.textLayer br{color:transparent;position:absolute;white-space:pre;cursor:text;transform-origin:0% 0%}
.textLayer span.markedContent{top:0;height:0}
.textLayer .src-on{background:rgba(253,224,71,.55);border-radius:2px}
.textLayer ::selection{background:rgba(0,100,255,.25)}
.src-box{position:absolute;z-index:1;background:rgba(253,224,71,.35);outline:2px solid #eab308;border-radius:2px;
pointer-events:none}
#pdf-status{color:#f9fafb;font:14px system-ui,sans-serif;text-align:center;padding:24px}
"""


@dataclass
class View:
    body: str  # the whole HTML document
    kind: str  # page | pdf | text
    nonce: str


def preview_url(doc_id: str, line_ids: list[str], lang: str) -> str:
    lines = "".join(f"&line={url_quote(lid, safe='')}" for lid in line_ids[:MAX_LINES])
    return f"/api/preview/{url_quote(doc_id, safe='')}?lang={lang}{lines}"


def preview_kind(doc_kind: str, url: str, has_file: bool = False) -> str:
    if doc_kind != "file":
        return "page"
    return "pdf" if has_file or is_pdf_url(url) else "text"


def fmt_date(value: str | None) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return datetime.now(UTC).strftime("%d.%m.%Y")


# ─────────────── where a page's HTML comes from ───────────────


class PageSource:
    """The crawled copy of a page (registry_pages.html_file under data/crawl/<site>/), else a live fetch."""

    def __init__(self, client: httpx.AsyncClient | None, pool: ConnectionPool | None, data_dir: Path = DATA_DIR):
        self.client = client
        self.pool = pool
        self.data_dir = data_dir
        self.cache: dict[str, tuple[float, str]] = {}

    def crawled(self, url: str) -> tuple[str, str] | None:
        """(html, fetched_at) of the crawled copy."""
        if self.pool is None:
            return None
        variants = list(dict.fromkeys([url, url.rstrip("/"), url.rstrip("/") + "/"]))
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    "SELECT site, html_file, fetched_at FROM registry_pages WHERE url = ANY(%s) "
                    "AND html_file IS NOT NULL ORDER BY fetched_at DESC LIMIT 1", (variants,)).fetchone()
        except psycopg.Error:
            return None
        if not row:
            return None
        path = self.data_dir / "crawl" / row[0] / row[1]
        if not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="replace"), row[2].astimezone(UTC).isoformat()

    async def live(self, url: str) -> str | None:
        hit = self.cache.get(url)
        if hit and time.monotonic() - hit[0] < LIVE_CACHE_S:
            return hit[1]
        if self.client is None:
            return None
        try:
            resp = await self.client.get(url, timeout=LIVE_TIMEOUT_S, headers={"User-Agent": CRAWLER_AGENT})
        except httpx.HTTPError as e:
            log.info("live fetch of %s failed: %s", url, e)
            return None
        if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", ""):
            return None
        self.cache[url] = (time.monotonic(), resp.text)
        return resp.text

    async def get(self, url: str) -> tuple[str, str, str] | None:
        """(html, how: crawl | live, date) or None."""
        if found := await asyncio.to_thread(self.crawled, url):
            return found[0], "crawl", found[1]
        if (text := await self.live(url)) is not None:
            return text, "live", datetime.now(UTC).isoformat()
        return None


# ─────────────── sanitizing a city hall page ───────────────

DROP = ("script", "noscript", "iframe", "frame", "frameset", "object", "embed", "applet", "portal", "base",
        "meta[http-equiv]", "link[rel=preload]", "link[rel=modulepreload]", "link[rel=prefetch]",
        "link[rel=dns-prefetch]", "link[rel=preconnect]", "link[rel=manifest]", "link[rel=import]")
URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href", "data", "poster", "background", "srcset"}
BAD_URL = re.compile(r"^\s*(javascript|vbscript|data:text/html)", re.I)


def sanitize(page_html: str) -> str:
    """The site's HTML without anything that runs: scripts, frames, plugins, its <base>, meta refresh/CSP, event
    handlers, javascript: URLs, form targets. Layout stays: CSS and images load from the original site."""
    tree = HTMLParser(page_html)
    for selector in DROP:
        for node in tree.css(selector):
            node.decompose()
    for node in tree.css("*"):
        attrs = node.attrs
        for name in list(attrs.keys()):
            low = name.lower()
            value = attrs[name] or ""
            if low.startswith("on") or low in ("srcdoc", "ping", "formaction") or (low in URL_ATTRS and BAD_URL.match(value)):
                del attrs[name]
        if node.tag == "form":
            for name in ("action", "method"):
                if name in attrs:
                    del attrs[name]
            attrs["target"] = "_blank"
        elif node.tag == "a":
            attrs["target"] = "_blank"
            attrs["rel"] = "noopener noreferrer"
    return tree.html or ""


WORDS = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    import unicodedata

    text = unicodedata.normalize("NFKC", text).replace("ş", "ș").replace("ţ", "ț").replace("Ş", "Ș").replace("Ţ", "Ț")
    return [w.lower() for w in WORDS.findall(text)]


def shown_text_has(page_html: str, quote: str) -> bool | None:
    """Whether the quote's first words are in the text a reader sees without the site's scripts (textarea, template
    and script data don't count); None when they aren't in the HTML at all (the page changed)."""
    head = " ".join(words(quote)[:8])
    if not head:
        return True
    tree = HTMLParser(page_html)
    everything = " ".join(words(tree.body.text(separator=" ") if tree.body else ""))
    for node in tree.css("script, style, template, textarea, noscript"):
        node.decompose()
    shown = " ".join(words(tree.body.text(separator=" ") if tree.body else ""))
    if head in shown:
        return True
    return False if head in everything else None


def inject(clean: str, *, base_url: str, head_extra: str, body_start: str, body_end: str) -> str:
    """Our <base>, style, banner and script into the sanitized page."""
    head = f'<meta charset="utf-8"><base href="{html.escape(base_url)}"><meta name="referrer" content="no-referrer">'
    if re.search(r"<head[^>]*>", clean, re.I):
        clean = re.sub(r"<head[^>]*>", lambda m: m.group(0) + head + head_extra, clean, count=1, flags=re.I)
    else:
        clean = f"<head>{head}{head_extra}</head>" + clean
    if re.search(r"<body[^>]*>", clean, re.I):
        clean = re.sub(r"<body[^>]*>", lambda m: m.group(0) + body_start, clean, count=1, flags=re.I)
    else:
        clean = body_start + clean
    if re.search(r"</body>", clean, re.I):  # a function, not a template: the JSON has backslashes
        return re.sub(r"</body>", lambda m: body_end + m.group(0), clean, count=1, flags=re.I)
    return clean + body_end


# ─────────────── the three views ───────────────


def data_block(payload: dict) -> str:
    # </script> can't appear inside the JSON: "<" escaped.
    return ('<script type="application/json" id="src-preview-data">'
            + json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c") + "</script>")


def banner(lang: str, *, label: str, open_text: str, open_href: str, embed: bool, extra: str = "") -> str:
    t = TEXT[lang]
    back = "" if embed else f'<a href="#" data-src-back>{t["back"]}</a>'
    return (f'<div id="src-preview-banner" role="note">{back}<span>{html.escape(label)}</span>'
            f'<a href="{html.escape(open_href)}" target="_blank" rel="noopener noreferrer">{open_text}</a>{extra}'
            '<span class="src-note" hidden></span></div><div id="src-preview-spacer"></div>')


def payload(doc: dict, lines: list[dict], selected: list[str], lang: str, allowed: list[str], mode: str) -> dict:
    return {"doc_id": doc["doc_id"], "kind": mode if mode != "text" else "text", "mode": mode, "lang": lang,
            "selected": selected, "allowed_origins": allowed, "text": TEXT[lang],
            "lines": {ln["line_id"]: {"text": ln["text"], "page": ln.get("page"), "bboxes": ln.get("bboxes", [])}
                      for ln in lines}}


def page_view(doc: dict, lines: list[dict], selected: list[str], lang: str, embed: bool, allowed: list[str],
              page_html: str, how: str, date: str, deep_link: str) -> View:
    """The page itself, or our text view when the quote is only in data the site's scripts would render (a page
    built by JavaScript shows an empty shell without them)."""
    quote = next((ln["text"] for ln in lines if ln["line_id"] in set(selected)), "")
    if quote and shown_text_has(page_html, quote) is False:
        return text_view(doc, lines, selected, lang, embed, allowed, deep_link, scripted=True)
    return _page_view(doc, lines, selected, lang, embed, allowed, page_html, how, date, deep_link)


def _page_view(doc: dict, lines: list[dict], selected: list[str], lang: str, embed: bool, allowed: list[str],
               page_html: str, how: str, date: str, deep_link: str) -> View:
    nonce = secrets.token_urlsafe(16)
    t = TEXT[lang]
    label = (t["copy"] if how == "crawl" else t["live"]).format(date=fmt_date(date))
    script = (data_block(payload(doc, lines, selected, lang, allowed, "page"))
              + f'<script nonce="{nonce}">{(STATIC / "preview.js").read_text(encoding="utf-8")}</script>')
    body = inject(sanitize(page_html), base_url=doc["url"], head_extra=f"<style>{STYLE}</style>",
                  body_start=banner(lang, label=label, open_text=t["open"], open_href=deep_link, embed=embed),
                  body_end=script)
    return View(body=body if body.lstrip().lower().startswith("<!doctype") else "<!doctype html>" + body,
                kind="page", nonce=nonce)


def text_view(doc: dict, lines: list[dict], selected: list[str], lang: str, embed: bool, allowed: list[str],
              deep_link: str, *, unavailable: bool = False, scripted: bool = False) -> View:
    """Our own view of the indexed lines: DOCX and other files, a page that couldn't be loaded, a page whose
    content only its own scripts render."""
    nonce = secrets.token_urlsafe(16)
    t = TEXT[lang]
    wanted = set(selected)
    rows = "".join(
        f'<p class="src-line{" src-on" if ln["line_id"] in wanted else ""}" id="line-{html.escape(ln["line_id"])}">'
        f"{html.escape(ln['text'])}</p>" for ln in lines)
    sub = t["unavailable"] if unavailable else t["scripted"] if scripted else t["saved_text"]
    page_like = unavailable or scripted
    open_text = t["open"] if page_like else t["download"]
    body = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">'
            f"<title>{html.escape(doc.get('title') or doc['url'])}</title>"
            f"<style>{STYLE}{TEXT_VIEW_STYLE}</style></head><body>"
            + banner(lang, label=t["copy"].format(date=fmt_date(doc.get("indexed_at"))), open_text=open_text,
                     open_href=deep_link if page_like else doc["url"], embed=embed)
            + f'<main><h1>{html.escape(doc.get("title") or doc["url"])}</h1><div class="src-sub">{sub}</div>{rows}</main>'
            + data_block(payload(doc, lines, selected, lang, allowed, "text"))
            + f'<script nonce="{nonce}">{(STATIC / "preview.js").read_text(encoding="utf-8")}</script></body></html>')
    return View(body=body, kind="text", nonce=nonce)


def pdf_view(doc: dict, lines: list[dict], selected: list[str], lang: str, embed: bool, allowed: list[str],
             file_url: str, deep_link: str, *, static_prefix: str = STATIC_PREFIX, file_data: str | None = None) -> View:
    nonce = secrets.token_urlsafe(16)
    t = TEXT[lang]
    first_page = next((ln.get("page") for ln in lines if ln["line_id"] in set(selected) and ln.get("page")), None)
    label = t["copy"].format(date=fmt_date(doc.get("indexed_at"))) + (
        f" · {t['page'].format(n=first_page)}" if first_page else "")
    data = payload(doc, lines, selected, lang, allowed, "pdf") | {
        "file_url": file_url, "page_sizes": doc.get("page_sizes") or [], "first_page": first_page}
    if file_data:
        data["file_data"] = file_data
    body = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">'
            f"<title>{html.escape(doc.get('title') or doc['url'])}</title><style>{STYLE}{PDF_STYLE}</style></head><body>"
            + banner(lang, label=label, open_text=t["open_pdf"], open_href=deep_link, embed=embed)
            + f'<div id="pdf-status">{t["loading"]}</div><div id="pages"></div>'
            + data_block(data)
            + f'<script type="module" nonce="{nonce}" src="{static_prefix}/pdf-viewer.js"></script></body></html>')
    return View(body=body, kind="pdf", nonce=nonce)


def csp(view: View, frame_ancestors: list[str]) -> str:
    ancestors = "*" if "*" in frame_ancestors else " ".join(["'self'", *frame_ancestors])
    if view.kind == "pdf":
        script = f"'nonce-{view.nonce}' 'self' 'wasm-unsafe-eval'"  # pdf.js decodes scans with WebAssembly
        extra = "worker-src 'self' blob:; "
        style = "'self' 'unsafe-inline'"
    else:
        script = f"'nonce-{view.nonce}'"
        extra = ""
        style = "* 'unsafe-inline'"
    return (f"default-src 'none'; img-src * data: blob:; style-src {style}; font-src * data:; script-src {script}; "
            f"{extra}connect-src 'self'; frame-ancestors {ancestors}")


def headers(view: View, frame_ancestors: list[str]) -> dict[str, str]:
    return {"Content-Security-Policy": csp(view, frame_ancestors), "Cache-Control": "private, max-age=600",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def deep_link_for(doc: dict, lines: list[dict], selected: list[str], kind: str) -> str:
    from spott.core.links import make_deep_link

    first = next((ln for ln in lines if ln["line_id"] in set(selected)), None)
    if not first:
        return doc["url"]
    return make_deep_link(doc["url"], first["text"], first.get("page") if kind == "pdf" else None) or doc["url"]


def host(url: str) -> str:
    return urlsplit(url).netloc
