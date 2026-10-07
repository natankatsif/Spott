"""A job that parses one new page of a known site still drops the site's menu (boilerplate counted over the
site's other crawled pages)."""

import json
from pathlib import Path

from spott.ingest.common.registry import Registry, now
from spott.ingest.parsing.html import parse_site_pages

REPEATED = ("Abonează-te la newsletterul nostru pentru a afla primul despre proiectele noi ale direcției și "
            "despre evenimentele organizate în oraș.")


def page_html(body: str) -> str:
    return (f"<html lang='ro'><head><title>t</title></head><body>"
            f"<main><h1>Pagina</h1><p>{body}</p><p>{REPEATED}</p></main></body></html>")


def test_one_new_page_still_loses_the_site_menu(tmp_path: Path, registry: Registry):
    reg = registry
    (tmp_path / "crawl" / "a.md" / "html").mkdir(parents=True)
    for i in range(5):
        body = (f"Conținutul unic al paginii numărul {i}, cu informații despre serviciul municipal {i} "
                f"și condițiile de acordare, termenele de depunere și actele necesare pentru cetățeni. ") * 3
        f = f"html/p{i}.html"
        (tmp_path / "crawl" / "a.md" / f).write_text(page_html(body), encoding="utf-8")
        reg.upsert_page({"url": f"https://a.md/p{i}", "site": "a.md", "status": 200, "html_file": f,
                         "fetched_at": now(), "title": "t", "lang": "ro"})
    rows = reg.site_pages("a.md")
    out = tmp_path / "parsed" / "pages"

    def texts(context):
        parse_site_pages([rows[0]], tmp_path, {"a.md": "other"}, reg, out, context=context)
        [f] = out.glob("*.json")
        return " ".join(b["text"] for b in json.loads(f.read_text(encoding="utf-8"))["blocks"])

    assert "newsletterul" in texts(None)  # alone, one page can't tell what repeats on every page
    assert "newsletterul" not in texts(reg.site_pages("a.md"))  # with the site's pages as context it can
    assert "serviciul municipal 0" in texts(reg.site_pages("a.md"))
