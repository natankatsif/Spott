"""HTML fixes applied before trafilatura, for markup it flattens into text without meaning.

- Data embedded as JSON in a hidden <textarea> (dgaurf.md services: title, fee, term, required documents)
  is turned into proper blocks instead of leaking into the page text as raw JSON with HTML tags.
- Price cards ("<ul><li>Abonament 1 lună</li><li>273 LEI</li></ul>" under "<h2>Călători generali</h2>")
  become one line "Călători generali — Abonament 1 lună: 273 LEI", so a price never loses its label.
- List items inside accordions/toggles (Elementor, <details>) carry the accordion title, so they keep their
  meaning: "Categorii … vor călători gratuit: Pensionarii pentru limita de vârstă".
"""

import html
import json
import re

from selectolax.parser import HTMLParser, Node

from spott.ingest.common.normalize import normalize_text
from spott.ingest.common.text import has_contacts

from .metadata import find_date

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
PRICE = re.compile(r"^\d[\d\s.,]*\s*(lei|mdl)\.?$", re.I)
TOGGLE_TITLES = ".elementor-tab-title, .elementor-toggle-title, summary"
TOGGLE_ITEMS = ".elementor-toggle-item, .elementor-accordion-item, details"


def squash(text: str) -> str:
    return normalize_text(" ".join(text.split()))


def make_node(tag: str, text: str) -> Node:
    return HTMLParser(f"<{tag}>{html.escape(text)}</{tag}>").css_first(tag)


def block(kind: str, text: str, **extra) -> dict:
    return {"type": kind, "text": text, "has_contacts": has_contacts(text)} | extra


def html_fragment_blocks(fragment: str) -> list[dict]:
    """<p> → paragraph, <li> → list_item; nested paragraphs inside list items are not repeated.
    <br> splits a paragraph into lines ("Termen: 30 zile<br>Tarif: 200 lei" → two lines)."""
    tree = HTMLParser(re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I))
    blocks = []
    for node in tree.css("p, li"):
        if node.tag == "p" and any(parent.tag == "li" for parent in node_parents(node)):
            continue
        kind = "list_item" if node.tag == "li" else "paragraph"
        for line in node.text().split("\n"):
            if text := squash(line):
                blocks.append(block(kind, text))
    return blocks


def node_parents(node: Node):
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def record_blocks(record: dict) -> list[dict]:
    """One embedded record (a service) → heading + its HTML fields + attachments."""
    blocks = [block("heading", squash(record["title"]), level=2)]
    html_fields = [k for k, v in record.items() if k.endswith("_html") and isinstance(v, str) and v.strip()]
    for key in html_fields:
        blocks += html_fragment_blocks(record[key])
    if "fees_html" not in html_fields:  # short fields only when the full text isn't there
        for key, label in (("fee", "Tarif"), ("term", "Termen")):
            if record.get(key):
                blocks.append(block("paragraph", f"{label}: {squash(str(record[key]))}"))
    for item in record.get("appendix") or []:
        if isinstance(item, dict) and item.get("url"):
            blocks.append(block("list_item", f"{item.get('title') or 'Anexă'}: {item['url']}"))
    return blocks


def extract_embedded_records(tree: HTMLParser) -> list[dict]:
    """Hidden <textarea> data islands; they are removed from the page either way (never visible text)."""
    blocks = []
    for node in tree.css("textarea[hidden]"):
        try:
            data = json.loads(node.text())
        except ValueError:
            data = None
        node.decompose()
        if isinstance(data, list):
            for record in data:
                if isinstance(record, dict) and isinstance(record.get("title"), str) and record["title"].strip():
                    blocks += record_blocks(record)
    return blocks


def rewrite_price_lists(tree: HTMLParser) -> None:
    root = tree.body or tree.root
    if root is None:
        return
    heading = ""
    rewrites = []
    for node in root.traverse():
        if node.tag in HEADINGS:
            heading = squash(node.text())
        elif node.tag == "ul":
            items = [c for c in node.iter() if c.tag == "li"]
            if len(items) == 2 and PRICE.match(price := squash(items[1].text())):
                label = squash(items[0].text())
                price = re.sub(r"(\d)\s*(lei|mdl)", r"\1 \2", price, flags=re.I)
                rewrites.append((node, f"{heading} — {label}: {price}" if heading else f"{label}: {price}"))
    for node, text in rewrites:
        node.replace_with(make_node("p", text))


def prefix_toggle_items(tree: HTMLParser) -> None:
    """Trafilatura drops accordion titles as navigation, so each list item inside carries its title:
    "Categorii … vor călători gratuit …: Pensionarii pentru limita de vârstă"."""
    for item in tree.css(TOGGLE_ITEMS):
        title = item.css_first(TOGGLE_TITLES)
        if title is None or not (title_text := squash(title.text()).rstrip(" :")):
            continue
        for li in item.css("li"):
            if (text := squash(li.text())) and not text.startswith(title_text):
                li.replace_with(make_node("li", f"{title_text}: {text}"))


def rewrite_toggle_titles(tree: HTMLParser) -> None:
    rewritten: set[int] = set()
    for node in tree.css(TOGGLE_TITLES):
        if any(p.mem_id in rewritten for p in node_parents(node)):
            continue  # inside a title already rewritten with its text
        if text := squash(node.text()):
            rewritten.add(node.mem_id)
            node.replace_with(make_node("h3", text))


def preprocess_html(html_content: str) -> tuple[str, list[dict]]:
    """Returns the HTML for trafilatura and the blocks recovered from embedded data."""
    tree = HTMLParser(html_content)
    embedded = extract_embedded_records(tree)
    rewrite_price_lists(tree)
    prefix_toggle_items(tree)
    rewrite_toggle_titles(tree)
    return tree.html or html_content, embedded


# Where a page states its own publication date; a bare <time> can belong to a sidebar of other posts.
PUBLISHED = (
    ('meta[property="article:published_time"]', "content"),
    ('meta[itemprop="datePublished"]', "content"),
    ('meta[name="date"]', "content"),
    ("article time[datetime]", "datetime"),
    ("main time[datetime]", "datetime"),
)


def publication_date(html_content: str) -> str | None:
    """ISO date the page was published, or None."""
    tree = HTMLParser(html_content)
    for selector, attr in PUBLISHED:
        node = tree.css_first(selector)
        value = (node.attributes.get(attr) or "").strip() if node is not None else ""
        if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", value):
            return "-".join(m.groups())
        if value and (date := find_date(value)):
            return date
    return None
