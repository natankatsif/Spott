"""Static source previews for the frontend's mock mode (docs/history/tasks/11 §3).

For every citation in <frontend>/src/lib/mocks/ask/*.json this renders the same HTML that GET /api/preview/{doc_id}
returns, saves it to <frontend>/public/mocks/preview/<mock>-<citation>.html and points the citation's preview_url at
it, so mock mode shows a real, highlighted preview without the backend.

Documents and lines come from the index in Postgres, like the endpoint's (the cited documents must be indexed),
PDFs from data/raw, crawled copies of pages through the registry. PDFs are copied next to the pages (files/<sha>.pdf)
with pdf.js (static/), all paths relative, so the folder works from any origin. No LLM, no network.

    cd backend && uv run python scripts/export_previews.py ../frontend

The frontend directory is an argument: the backend doesn't know where (or whether) a frontend lives.
"""

import argparse
import json
import shutil
from pathlib import Path

from spott.api import preview
from spott.api.files import raw_pdf
from spott.api.store import PgStore
from spott.core.db import get_pool

MOCKS = Path()  # <frontend>/src/lib/mocks/ask, set by main()
OUT = Path()  # <frontend>/public/mocks/preview
STORE: PgStore  # the index, set by main()
PAGES: preview.PageSource  # crawled copies of pages, set by main()
PUBLIC_PREFIX = "/mocks/preview"
# Mock mode is served by the frontend itself (same origin as the preview); any origin may talk to a static demo.
ALLOWED = ["*"]


def load_doc(doc_id: str) -> tuple[dict, list[dict]]:
    """The document and its lines in reading order, as GET /api/preview/{doc_id} has them."""
    doc = STORE.preview_document(doc_id)
    if doc is None:
        raise SystemExit(f"{doc_id}: not in the index (run the indexing pipeline first)")
    return doc, [preview.preview_line(r, doc.get("page_sizes") or []) for r in STORE.doc_lines(doc_id)]


def render(doc: dict, lines: list[dict], selected: list[str], lang: str) -> tuple[str, str]:
    """(html, preview_kind)."""
    kind = preview.preview_kind(doc["kind"], doc["url"], doc["has_file"])
    deep = preview.deep_link_for(doc, lines, selected, kind)
    common = {"doc": doc, "lines": lines, "selected": selected, "lang": lang, "embed": True, "allowed": ALLOWED}
    if kind == "pdf":
        pdf = raw_pdf(doc["doc_id"], doc["sha256"])
        (OUT / "files").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, OUT / "files" / pdf.name)
        view = preview.pdf_view(**common, file_url=f"files/{pdf.name}", deep_link=deep, static_prefix="static")
    elif kind == "page" and (got := PAGES.crawled(doc["url"])):
        view = preview.page_view(**common, page_html=got[0], how="crawl", date=got[1], deep_link=deep)
    else:
        view = preview.text_view(**common, deep_link=deep, unavailable=kind == "page")
    return view.body, view.kind


def main() -> None:
    global MOCKS, OUT, PAGES, STORE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frontend", type=Path, help="the frontend directory (its mocks are read and rewritten)")
    frontend = ap.parse_args().frontend
    MOCKS = frontend / "src" / "lib" / "mocks" / "ask"
    OUT = frontend / "public" / "mocks" / "preview"
    pool = get_pool(min_size=1, max_size=1)
    STORE, PAGES = PgStore(pool), preview.PageSource(None, pool)
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(preview.STATIC, OUT / "static", dirs_exist_ok=True)
    cache: dict[str, tuple[dict, list[dict]]] = {}
    for path in sorted(MOCKS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for c in data.get("citations", []):
            if c["doc_id"] not in cache:
                cache[c["doc_id"]] = load_doc(c["doc_id"])
            doc, lines = cache[c["doc_id"]]
            known = {ln["line_id"] for ln in lines}
            selected = [lid for lid in c["line_ids"] if lid in known][: preview.MAX_LINES]
            if not selected:
                print(f"  ! {path.stem} {c['id']}: its lines are not in the local index; text view without highlight")
            body, kind = render(doc, lines, selected, data.get("lang", "ro"))
            name = f"{path.stem}-{c['id']}.html"
            (OUT / name).write_text(body, encoding="utf-8")
            c["preview_url"] = f"{PUBLIC_PREFIX}/{name}"
            c["preview_kind"] = kind
            print(f"{path.stem:18} {c['id']:3} {kind:4} {len(body) // 1024:5} KB  {c['doc_id'][:70]}")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
