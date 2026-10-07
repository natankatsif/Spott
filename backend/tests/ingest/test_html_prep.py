"""HTML fixes before trafilatura: embedded JSON data, price cards, accordion lists."""

import json

from spott.ingest.parsing.html import extract_raw_blocks
from spott.ingest.parsing.html_prep import preprocess_html, publication_date

SERVICES = [{
    "title": "Emiterea certificatului privind edificarea construcției",
    "fee": "8000 lei",
    "term": "30 zile",
    "description_html": "<p style=text-align: justify;>Se depune cererea.</p>",
    "fees_html": "<p><strong>Termenul de emitere a documentului: </strong>30 zile<br>"
                 "<strong>Tariful: </strong>8000 lei</p>",
    "documents_html": "<ol><li><p>Copia buletinului.</p></li><li><p>Planul topografic.</p></li></ol>",
    "appendix": [{"title": "anexa-1.docx", "url": "https://dgaurf.md/storage/services/anexa-1.docx"}],
}]

SERVICES_PAGE = f"""<html><body><main>
<h1>Servicii</h1>
<textarea id="services-data" v-pre hidden>{json.dumps(SERVICES, ensure_ascii=False)}</textarea>
</main></body></html>"""

PRICES_PAGE = """<html><body><main><article>
<p>Tariful biletelor și abonamentelor de călătorie, începînd cu 01.05.2026.</p>
<h2>Călători generali</h2>
<div><ul><li><div>Bilet <small>1 călătorie</small></div></li><li><span>7<sup>LEI</sup></span></li></ul></div>
<div><ul><li><div>Abonament <small>1 lună</small></div></li><li><span>273<sup>LEI</sup></span></li></ul></div>
<h2>Pensionari (alte categorii)</h2>
<div><ul><li><div>Abonament <small>1 lună</small></div></li><li><span>164<sup>LEI</sup></span></li></ul></div>
<ul><li>Primul punct al unei liste obișnuite</li><li>Al doilea punct</li></ul>
</article></main></body></html>"""

ACCORDION_PAGE = """<html><body><main><article>
<p>Informație despre transportul public în municipiul Chișinău pentru toate categoriile de călători.</p>
<div class="elementor-toggle-item">
  <div class="elementor-tab-title"><a class="elementor-toggle-title">Categorii care călătoresc gratuit:</a></div>
  <div class="elementor-tab-content"><ul><li>Pensionarii pentru limita de vârstă;</li><li>Veteranii;</li></ul></div>
</div>
</article></main></body></html>"""


def texts(blocks):
    return [b["text"] for b in blocks]


def test_embedded_json_becomes_blocks_and_leaves_no_raw_markup():
    html, embedded = preprocess_html(SERVICES_PAGE)

    assert "fees_html" not in html
    assert embedded[0] == {"type": "heading", "level": 2, "has_contacts": False,
                           "text": "Emiterea certificatului privind edificarea construcției"}
    assert texts(embedded[1:]) == [
        "Se depune cererea.",
        "Termenul de emitere a documentului: 30 zile",  # <br> splits the paragraph
        "Tariful: 8000 lei",
        "Copia buletinului.",
        "Planul topografic.",
        "anexa-1.docx: https://dgaurf.md/storage/services/anexa-1.docx",
    ]
    assert [b["type"] for b in embedded[4:]] == ["list_item", "list_item", "list_item"]


def test_embedded_blocks_survive_when_trafilatura_finds_nothing():
    _, blocks = extract_raw_blocks(SERVICES_PAGE)
    assert "Tariful: 8000 lei" in texts(blocks)
    assert not any("<p>" in t or "_html" in t for t in texts(blocks))


def test_short_fields_used_when_there_is_no_fees_html():
    record = {k: v for k, v in SERVICES[0].items() if k != "fees_html"}
    page = SERVICES_PAGE.replace(json.dumps(SERVICES, ensure_ascii=False), json.dumps([record], ensure_ascii=False))
    _, embedded = preprocess_html(page)
    assert {"Tarif: 8000 lei", "Termen: 30 zile"} <= set(texts(embedded))


def test_price_cards_keep_label_and_category():
    html, _ = preprocess_html(PRICES_PAGE)
    assert "Călători generali — Bilet 1 călătorie: 7 LEI" in html
    assert "Călători generali — Abonament 1 lună: 273 LEI" in html
    assert "Pensionari (alte categorii) — Abonament 1 lună: 164 LEI" in html
    assert "<li>Primul punct al unei liste obișnuite</li>" in html  # ordinary lists untouched


def test_accordion_list_items_carry_the_title():
    _, blocks = extract_raw_blocks(ACCORDION_PAGE)
    assert "Categorii care călătoresc gratuit: Pensionarii pentru limita de vârstă;" in texts(blocks)
    assert "Categorii care călătoresc gratuit: Veteranii;" in texts(blocks)


def test_page_without_special_markup_is_unchanged_in_content():
    page = "<html><body><main><article><p>Program de lucru: luni–vineri, 8:00–17:00.</p></article></main></body></html>"
    _, blocks = extract_raw_blocks(page)
    assert texts(blocks) == ["Program de lucru: luni–vineri, 8:00–17:00."]


def test_publication_date_of_the_page_not_of_a_sidebar_post():
    page = """<html><head><meta property="article:published_time" content="2026-09-23T10:00:00+03:00"></head>
    <body><aside><time datetime="2024-01-01">old post</time></aside><article><p>Text</p></article></body></html>"""
    assert publication_date(page) == "2026-09-23"
    article = '<html><body><aside><time datetime="2024-01-01">x</time></aside>' \
              '<article><time datetime="2025-05-05T08:00">5 mai 2025</time></article></body></html>'
    assert publication_date(article) == "2025-05-05"
    assert publication_date('<html><body><aside><time datetime="2024-01-01">x</time></aside></body></html>') is None
