"""HTML page parsing with trafilatura, structural extraction, and boilerplate filtering.

Input: rows from registry `pages` table with status < 400 and html_file.
Output: data/parsed/pages/<url_hash>.json
"""

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import trafilatura
from selectolax.parser import HTMLParser

from common.registry import Registry
from common.urls import url_key

from common.text import format_table_markdown, has_contacts
from .normalize import detect_lang, normalize_lang, normalize_text

log = logging.getLogger("parsing.html")

MIN_PAGE_CHARS = 200
BOILERPLATE_THRESHOLD = 0.30


def extract_raw_blocks(html_content: str) -> tuple[str | None, list[dict]]:
    """Extracts candidate blocks and title from HTML using trafilatura."""
    xml_str = trafilatura.extract(
        html_content,
        output_format="xml",
        include_tables=True,
        include_formatting=True,
        favor_recall=True,
    )
    if not xml_str:
        return None, []

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None, []

    main_node = root.find("main")
    if main_node is None:
        return None, []

    raw_blocks: list[dict] = []
    page_title_from_h1: str | None = None

    for elem in list(main_node):
        tag = elem.tag.lower()
        if tag == "head":
            rend = elem.attrib.get("rend", "h1").lower()
            level = 1
            if rend.startswith("h") and rend[1:].isdigit():
                level = min(max(int(rend[1:]), 1), 6)
            text = normalize_text("".join(elem.itertext()))
            if not text:
                continue
            if level == 1 and not page_title_from_h1:
                page_title_from_h1 = text
            raw_blocks.append({
                "type": "heading",
                "level": level,
                "text": text,
                "has_contacts": has_contacts(text),
            })
        elif tag == "p":
            text = normalize_text("".join(elem.itertext()))
            if not text:
                continue
            raw_blocks.append({
                "type": "paragraph",
                "text": text,
                "has_contacts": has_contacts(text),
            })
        elif tag == "list":
            for item in elem.findall("item"):
                text = normalize_text("".join(item.itertext()))
                if not text:
                    continue
                raw_blocks.append({
                    "type": "list_item",
                    "text": text,
                    "has_contacts": has_contacts(text),
                })
        elif tag == "table":
            grid = []
            header = []
            for row_elem in elem.findall("row"):
                cells = []
                is_header_row = False
                for cell_elem in row_elem.findall("cell"):
                    c_text = normalize_text("".join(cell_elem.itertext()))
                    cells.append(c_text)
                    if cell_elem.attrib.get("role") == "head":
                        is_header_row = True
                if cells:
                    if is_header_row and not header:
                        header = cells
                    else:
                        grid.append(cells)
            if not header and grid:
                header = grid.pop(0)
            md_text = format_table_markdown(header, grid)
            if md_text:
                full_text = " ".join(header + [c for r in grid for c in r])
                raw_blocks.append({
                    "type": "table",
                    "header": header,
                    "rows": grid,
                    "text": md_text,
                    "has_contacts": has_contacts(full_text),
                })
        else:
            text = normalize_text("".join(elem.itertext()))
            if text:
                raw_blocks.append({
                    "type": "paragraph",
                    "text": text,
                    "has_contacts": has_contacts(text),
                })

    return page_title_from_h1, raw_blocks


def build_page_blocks(raw_blocks: list[dict], boilerplate_texts: set[str]) -> list[dict]:
    """Filters boilerplate (except contacts) and builds section hierarchies."""
    blocks: list[dict] = []
    section: list[tuple[int, str]] = []

    for raw in raw_blocks:
        text = raw["text"]
        norm_key = text.strip().lower()
        if norm_key in boilerplate_texts and not raw.get("has_contacts", False):
            continue

        is_heading = raw["type"] == "heading"
        if is_heading:
            level = raw.get("level", 1)
            while section and section[-1][0] >= level:
                section.pop()

        block = {
            "id": len(blocks),
            "type": raw["type"],
            "text": text,
            "page": None,
            "bboxes": [],
            "section": [t for _, t in section],
            "lang": normalize_lang(None, fallback_text=text),
            "has_contacts": raw.get("has_contacts", False),
        }
        if is_heading:
            block["level"] = raw.get("level", 1)
        if raw["type"] == "table":
            block["header"] = raw.get("header", [])
            block["rows"] = raw.get("rows", [])

        blocks.append(block)

        if is_heading:
            section.append((raw.get("level", 1), text))

    return blocks


def parse_site_pages(
    rows: list,
    data_dir: Path,
    categories: dict[str, str],
    registry: Registry,
    out_dir: Path,
) -> dict[str, int]:
    """Parses all pages for a given site, detects boilerplate, and writes JSON."""
    stats = {"parsed": 0, "empty": 0, "failed": 0, "boilerplate_dropped": 0}
    if not rows:
        return stats

    out_dir.mkdir(parents=True, exist_ok=True)

    # Pass 1: Read HTML and extract raw blocks
    page_data_list = []
    block_freq: Counter[str] = Counter()

    for row in rows:
        url = row["url"]
        site = row["site"]
        html_file = data_dir / "crawl" / site / row["html_file"]
        if not html_file.exists():
            registry.mark_page_parsed(url, "failed", error="html file missing")
            stats["failed"] += 1
            continue

        try:
            body_bytes = html_file.read_bytes()
            html_hash = hashlib.sha1(body_bytes).hexdigest()
            html_text = body_bytes.decode("utf-8", errors="replace")
            h1_title, raw_blocks = extract_raw_blocks(html_text)

            # Record frequency of block texts across pages of this site
            seen_texts_on_page = set()
            for b in raw_blocks:
                key = b["text"].strip().lower()
                if key and key not in seen_texts_on_page:
                    seen_texts_on_page.add(key)
                    block_freq[key] += 1

            page_data_list.append({
                "row": row,
                "html_hash": html_hash,
                "h1_title": h1_title,
                "raw_blocks": raw_blocks,
            })
        except Exception as e:
            log.warning("Failed parsing HTML for %s: %s", url, e)
            registry.mark_page_parsed(url, "failed", error=str(e))
            stats["failed"] += 1

    num_pages = len(page_data_list)
    # If >= 3 pages, blocks appearing on > 30% pages are boilerplate
    boilerplate_texts: set[str] = set()
    if num_pages >= 3:
        threshold = num_pages * BOILERPLATE_THRESHOLD
        boilerplate_texts = {text for text, count in block_freq.items() if count > threshold}

    # Pass 2: Filter boilerplate and save pages
    for pdata in page_data_list:
        row = pdata["row"]
        url = row["url"]
        site = row["site"]
        category = categories.get(site, "transparency")
        raw_blocks = pdata["raw_blocks"]

        # Count boilerplate dropped
        for b in raw_blocks:
            k = b["text"].strip().lower()
            if k in boilerplate_texts and not b.get("has_contacts", False):
                stats["boilerplate_dropped"] += 1

        blocks = build_page_blocks(raw_blocks, boilerplate_texts)
        total_chars = sum(len(b["text"]) for b in blocks)

        if total_chars < MIN_PAGE_CHARS:
            registry.mark_page_parsed(url, "empty", html_hash=pdata["html_hash"])
            stats["empty"] += 1
            continue

        # Determine title
        title = pdata["h1_title"] or row["title"] or ""
        # Determine language
        body_text = "\n".join(b["text"] for b in blocks)
        lang = normalize_lang(row["lang"], fallback_text=body_text)

        # Determine alternates
        try:
            alternates = json.loads(row["alternates"] or "{}")
        except Exception:
            alternates = {}

        ukey = url_key(url)
        page_doc = {
            "id": f"page:{ukey}",
            "doc_id": f"page:{ukey}",
            "kind": "page",
            "url": url,
            "site": site,
            "category": category,
            "title": title,
            "lang": lang,
            "alternates": alternates,
            "pages": [],
            "stats": {
                "blocks": len(blocks),
                "chars": total_chars,
            },
            "blocks": blocks,
        }

        url_hash = hashlib.sha1(ukey.encode()).hexdigest()[:20]
        out_file = out_dir / f"{url_hash}.json"
        out_file.write_text(json.dumps(page_doc, ensure_ascii=False, indent=1), encoding="utf-8")

        registry.mark_page_parsed(url, "parsed", html_hash=pdata["html_hash"])
        stats["parsed"] += 1

    return stats
