"""Document and page chunker.

Transforms parsed files (data/parsed/*.json) and parsed pages (data/parsed/pages/*.json)
into chunks respecting legal hierarchy, section headers, tables, language boundaries,
and length constraints.
"""

import hashlib
import re

from spott.ingest.common.text import check_contacts, format_table_markdown
from spott.ingest.common.urls import url_key
from spott.ingest.parsing.normalize import normalize_lang

from .legal import LegalHierarchyTracker, is_act_or_has_major_legal

MAX_MERGE_CHARS = 1500
MAX_BLOCK_CHARS = 2500
OVERLAP_CHARS = 200


def split_long_text(text: str, target_size: int = 1500, max_size: int = 2500, overlap: int = 200) -> list[str]:
    """Splits text exceeding max_size into units of ~target_size with ~overlap."""
    if len(text) <= max_size:
        return [text]

    tokens = re.split(r"(\n\n+|\n|(?<=[.!?])\s+)", text)
    units = []
    unit_max = target_size // 2
    for t in tokens:
        if not t:
            continue
        if len(t) > unit_max:
            words = re.split(r"(\s+)", t)
            buf = ""
            for w in words:
                if len(buf) + len(w) > unit_max:
                    if buf:
                        units.append(buf)
                        buf = ""
                    if len(w) > unit_max:
                        for k in range(0, len(w), unit_max):
                            units.append(w[k:k + unit_max])
                    else:
                        buf = w
                else:
                    buf += w
            if buf:
                units.append(buf)
        else:
            units.append(t)

    chunks = []
    cur_chunk = []
    cur_len = 0
    i = 0
    while i < len(units):
        u = units[i]
        if cur_chunk and (cur_len + len(u) > max_size or cur_len >= target_size):
            c_str = "".join(cur_chunk).strip()
            if c_str:
                chunks.append(c_str)
            overlap_len = 0
            back_idx = len(cur_chunk) - 1
            while back_idx >= 0 and overlap_len < overlap:
                overlap_len += len(cur_chunk[back_idx])
                back_idx -= 1
            cur_chunk = cur_chunk[max(0, back_idx + 1):]
            cur_len = sum(len(x) for x in cur_chunk)
        cur_chunk.append(u)
        cur_len += len(u)
        i += 1

    if cur_chunk:
        c_str = "".join(cur_chunk).strip()
        if c_str:
            chunks.append(c_str)

    return chunks or [text]


def chunk_table_block(table_block: dict, target_size: int = 1500) -> list[str]:
    """Splits a table block into row groups, repeating the header in each chunk."""
    header = table_block.get("header") or []
    rows = table_block.get("rows") or []
    if not rows:
        return [table_block.get("text", "")]

    full_text = format_table_markdown(header, rows)
    if len(full_text) <= MAX_BLOCK_CHARS:
        return [full_text]

    chunks = []
    cur_rows = []
    for row in rows:
        cur_rows.append(row)
        tbl_text = format_table_markdown(header, cur_rows)
        if len(tbl_text) >= target_size:
            chunks.append(tbl_text)
            cur_rows = []
    if cur_rows:
        chunks.append(format_table_markdown(header, cur_rows))
    return chunks or [full_text]


def make_citation_label(meta: dict, kind: str, legal_path: list[str], section: list[str]) -> str:
    """Builds human-readable citation label according to the specification."""
    title = meta.get("title") or ""
    if kind == "page":
        if section:
            return f"{title} › {' › '.join(section)}"
        return title

    # For files: "Decizia nr. 12/3 din 2023-03-14 › Anexa nr. 1 › pct. 12"
    doc_type = meta.get("doc_type")
    number = meta.get("number")
    date = meta.get("date")

    if doc_type and (number or date):
        t = doc_type.capitalize()
        if number:
            t += f" nr. {number}"
        if date:
            t += f" din {date}"
        base_label = t
    elif title:
        base_label = title[:80]
    else:
        base_label = "Document"

    parts = [base_label]
    if legal_path:
        parts.extend(legal_path)
    elif section:
        parts.extend(section)

    return " › ".join(parts)


def extract_chunk_lines(chunk: dict, blocks: list[dict] | None = None) -> list[dict]:
    """Extracts search & citation lines from a chunk.

    Rules:
    - Split by newline
    - Line > 400 chars: split by sentences
    - Line < 25 chars: merge with next line
    - Table: row by row with column headers in embed_text
    - Text of lines is verbatim (preserves all tokens)
    - Returns list of line dicts with idx, line_id, text, embed_text, etc.
    """
    text = chunk.get("text", "").strip()
    if not text:
        return []

    chunk_id = chunk.get("chunk_id", "")
    doc_id = chunk.get("doc_id", "")
    title = chunk.get("title") or ""
    citation_label = chunk.get("citation_label") or ""
    prefix = f"{title} › {citation_label}".strip(" ›").strip()

    is_table = chunk.get("is_table", False)
    raw_blocks = blocks if blocks is not None else chunk.get("blocks", [])
    block_ids = chunk.get("block_ids", [])
    default_block_id = str(block_ids[0]) if block_ids else None
    pages = chunk.get("pages", [])
    default_page = pages[0] if (pages and isinstance(pages, list)) else None
    default_bboxes = chunk.get("bboxes", [])
    lang = chunk.get("lang")

    lines_data: list[tuple[str, str, str | None, int | None, list]] = []

    if is_table:
        raw_rows = [r.strip() for r in text.split("\n") if r.strip()]
        header_cols: list[str] = []
        data_rows: list[str] = []
        for r in raw_rows:
            if re.match(r"^\|[\s\-:|]+\|$", r):
                continue
            if not header_cols and r.startswith("|"):
                header_cols = [c.strip() for c in r.split("|")[1:-1]]
            else:
                data_rows.append(r)

        if not data_rows:
            data_rows = raw_rows

        for r in data_rows:
            row_cols = [c.strip() for c in r.split("|")[1:-1]] if r.startswith("|") else []
            if header_cols and row_cols and len(header_cols) == len(row_cols):
                desc_parts = [f"{h}: {c}" for h, c in zip(header_cols, row_cols, strict=False) if c]
                row_desc = " | ".join(desc_parts) if desc_parts else r
            else:
                row_desc = r
            embed_text = f"{prefix}\n{row_desc}" if prefix else row_desc
            lines_data.append((r, embed_text, default_block_id, default_page, default_bboxes))
    else:
        raw_lines = [l.strip() for l in text.split("\n") if l.strip()]
        sentences: list[str] = []
        for l in raw_lines:
            if len(l) > 400:
                parts = re.split(r"(?<=[.!?])\s+", l)
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    if len(p) > 400:
                        words = p.split()
                        cur_w: list[str] = []
                        cur_l = 0
                        for w in words:
                            if cur_w and cur_l + len(w) + 1 > 350:
                                sentences.append(" ".join(cur_w))
                                cur_w = [w]
                                cur_l = len(w)
                            else:
                                cur_w.append(w)
                                cur_l += len(w) + 1
                        if cur_w:
                            sentences.append(" ".join(cur_w))
                    else:
                        sentences.append(p)
            else:
                sentences.append(l)

        merged_lines: list[str] = []
        buf = ""
        for s in sentences:
            if buf:
                buf = f"{buf} {s}"
                if len(buf) >= 25:
                    merged_lines.append(buf)
                    buf = ""
            elif len(s) < 25:
                buf = s
            else:
                merged_lines.append(s)
        if buf:
            if merged_lines:
                merged_lines[-1] = f"{merged_lines[-1]} {buf}"
            else:
                merged_lines.append(buf)

        for line_str in merged_lines:
            m_block_id = default_block_id
            m_page = default_page
            m_bboxes = default_bboxes
            for b in raw_blocks:
                b_txt = b.get("text", "")
                if line_str in b_txt or (b_txt and b_txt in line_str):
                    m_block_id = str(b.get("id")) if b.get("id") is not None else default_block_id
                    m_page = b.get("page", default_page)
                    m_bboxes = b.get("bboxes", default_bboxes)
                    break

            embed_text = f"{prefix}\n{line_str}" if prefix else line_str
            lines_data.append((line_str, embed_text, m_block_id, m_page, m_bboxes))

    result = []
    for idx, (l_text, l_embed, b_id, pg, bbox) in enumerate(lines_data):
        line_id = hashlib.sha1(f"{chunk_id}:{idx}".encode()).hexdigest()
        c_hash = hashlib.sha1(l_text.encode("utf-8")).hexdigest()
        result.append({
            "line_id": line_id,
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "idx": idx,
            "text": l_text,
            "embed_text": l_embed,
            "lang": lang,
            "block_id": b_id,
            "page": pg,
            "bboxes": bbox or [],
            "content_hash": c_hash,
        })

    return result


def build_chunk(
    *,
    doc_id: str,
    kind: str,
    text: str,
    blocks: list[dict],
    meta: dict,
    parser_version: str,
    part_idx: int = 0,
    is_table: bool = False,
    override_legal_path: list[str] | None = None,
    override_section: list[str] | None = None,
    page_sizes: list[dict] | None = None,
) -> dict:
    first_block_id = blocks[0]["id"] if blocks and "id" in blocks[0] else 0
    last_block_id = blocks[-1]["id"] if blocks and "id" in blocks[-1] else 0
    ord_val = int(first_block_id) if isinstance(first_block_id, int) or (isinstance(first_block_id, str) and first_block_id.isdigit()) else 0

    chunk_id_raw = f"{doc_id}:{first_block_id}:{last_block_id}:{part_idx}:{parser_version}"
    chunk_id = hashlib.sha1(chunk_id_raw.encode("utf-8")).hexdigest()

    if override_section is not None:
        section = override_section
    else:
        content_blocks = [b for b in blocks if b.get("type") not in ("heading", "title", "section_header")]
        if content_blocks:
            section = content_blocks[0].get("section", [])
        elif blocks:
            last_h = blocks[-1]
            last_text = last_h.get("text", "").strip()
            section = list(last_h.get("section", [])) + ([last_text] if last_text else [])
        else:
            section = []

    if override_legal_path is not None:
        legal_path = override_legal_path
    else:
        content_blocks = [b for b in blocks if b.get("type") not in ("heading", "title", "section_header") and b.get("legal_path")]
        if content_blocks:
            legal_path = content_blocks[0].get("legal_path", [])
        elif blocks:
            legal_path = blocks[0].get("legal_path", [])
        else:
            legal_path = []

    parent_legal_path = legal_path[:-1] if legal_path else []

    citation_label = make_citation_label(meta, kind, legal_path, section)
    title = meta.get("title") or ""
    embed_text = f"{title}\n{citation_label}\n{text}"

    block_ids = [b["id"] for b in blocks if "id" in b]
    pages = sorted({b["page"] for b in blocks if b.get("page") is not None})
    bboxes = [bbox for b in blocks for bbox in b.get("bboxes", [])]

    content_blocks_with_lang = [b for b in blocks if b.get("type") not in ("heading", "title", "section_header") and b.get("lang")]
    if content_blocks_with_lang:
        raw_lang = content_blocks_with_lang[0].get("lang")
    else:
        raw_lang = blocks[0].get("lang") if blocks else meta.get("lang")
    lang = normalize_lang(raw_lang, fallback_text=text)

    has_contacts = any(b.get("has_contacts", False) for b in blocks) or check_contacts(text)

    chunk_dict = {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "kind": kind,
        "text": text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "section": section,
        "legal_path": legal_path,
        "parent_legal_path": parent_legal_path,
        "page_sizes": page_sizes or [],
        "block_ids": block_ids,
        "pages": pages,
        "bboxes": bboxes,
        "lang": lang,
        "char_count": len(text),
        "content_hash": hashlib.sha1(text.encode("utf-8")).hexdigest(),
        "has_contacts": has_contacts,
        "is_table": is_table,
        "title": title,
        "doc_type": meta.get("doc_type"),
        "number": meta.get("number"),
        "date": meta.get("date"),
        "category": meta.get("category"),
        "site": meta.get("site"),
        "url": meta.get("url"),
        "found_on": meta.get("found_on"),
        "ord": ord_val,
        "sha256": meta.get("sha256"),
        "previous_sha256": meta.get("previous_sha256"),
        "version": meta.get("version", 1),
        "updated_at": meta.get("updated_at"),
    }
    chunk_dict["lines"] = extract_chunk_lines(chunk_dict, blocks=blocks)
    return chunk_dict



def merge_two_chunks(a: dict, b: dict, parser_version: str = "2") -> dict:
    """Merges two adjacent chunks from the same document and section."""
    merged_text = (a["text"] + "\n\n" + b["text"]).strip()
    block_ids = list(dict.fromkeys(a.get("block_ids", []) + b.get("block_ids", [])))
    pages = sorted(set(a.get("pages", []) + b.get("pages", [])))
    bboxes = a.get("bboxes", []) + b.get("bboxes", [])
    has_contacts = a.get("has_contacts", False) or b.get("has_contacts", False)

    first_block_id = block_ids[0] if block_ids else 0
    last_block_id = block_ids[-1] if block_ids else 0
    doc_id = a["doc_id"]
    part_idx = 0
    chunk_id_raw = f"{doc_id}:{first_block_id}:{last_block_id}:{part_idx}:{parser_version}"
    chunk_id = hashlib.sha1(chunk_id_raw.encode("utf-8")).hexdigest()

    citation_label = a.get("citation_label") or b.get("citation_label") or ""
    title = a.get("title") or b.get("title") or ""
    embed_text = f"{title}\n{citation_label}\n{merged_text}"

    merged = dict(a)
    merged.update({
        "chunk_id": chunk_id,
        "text": merged_text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "parent_legal_path": a.get("parent_legal_path", []),
        "page_sizes": a.get("page_sizes", []),
        "block_ids": block_ids,
        "pages": pages,
        "bboxes": bboxes,
        "char_count": len(merged_text),
        "content_hash": hashlib.sha1(merged_text.encode("utf-8")).hexdigest(),
        "has_contacts": has_contacts,
        "ord": a.get("ord", 0),
    })
    merged["lines"] = extract_chunk_lines(merged)
    return merged


SUB_ARTICLE_PAT = re.compile(
    r"^(?:pct\.|п\.|alin\.|ч\.|lit\.|подп\.)(?:\s|$)",
    re.IGNORECASE,
)
LEGAL_ITEM_PAT = re.compile(
    r"^(.*?\b(?:pct\.|п\.|alin\.|ч\.|lit\.|подп\.)\s*)([\d]+(?:\.[\d]+)*|[a-zA-Zа-яА-Я])(\)?)",
    re.IGNORECASE,
)


def make_range_label(first: str, last: str) -> str:
    """Creates a range label from first and last legal item labels (e.g. 'pct. 2' and 'pct. 4' -> 'pct. 2–4')."""
    m1 = LEGAL_ITEM_PAT.match(first)
    m2 = LEGAL_ITEM_PAT.match(last)
    if m1 and m2:
        prefix1, val1, s1 = m1.group(1), m1.group(2), m1.group(3)
        val2, s2 = m2.group(2), m2.group(3)
        if s1 == ")" and s2 == ")":
            return f"{prefix1}{val1})–{val2})"
        return f"{prefix1}{val1}–{val2}{s2}"
    return f"{first}–{last}"


def are_compatible_legal_items(a_last: str, b_last: str) -> bool:
    """Checks if two legal item identifiers belong to the same level/type (e.g. both are pct.)."""
    if not (SUB_ARTICLE_PAT.match(a_last) and SUB_ARTICLE_PAT.match(b_last)):
        return False
    m1 = LEGAL_ITEM_PAT.match(a_last)
    m2 = LEGAL_ITEM_PAT.match(b_last)
    if not (m1 and m2):
        return False
    return m1.group(1).strip().lower() == m2.group(1).strip().lower()


def merge_legal_group(group: list[dict], parser_version: str = "2") -> dict:
    """Merges a sequence of short legal item chunks with the same parent."""
    first = group[0]
    last = group[-1]
    merged_text = "\n".join(c["text"] for c in group)
    parent = first.get("legal_path", [])[:-1]
    first_last = first["legal_path"][-1]
    final_last = last["legal_path"][-1]
    range_last = make_range_label(first_last, final_last)
    merged_legal_path = parent + [range_last]

    meta = {
        "title": first.get("title"),
        "doc_type": first.get("doc_type"),
        "number": first.get("number"),
        "date": first.get("date"),
        "category": first.get("category"),
        "site": first.get("site"),
        "url": first.get("url"),
        "found_on": first.get("found_on"),
    }
    kind = first.get("kind", "file")
    section = first.get("section", [])
    citation_label = make_citation_label(meta, kind, merged_legal_path, section)
    title = meta.get("title") or ""
    embed_text = f"{title}\n{citation_label}\n{merged_text}"

    block_ids = list(dict.fromkeys(b_id for c in group for b_id in c.get("block_ids", [])))
    pages = sorted(set(p for c in group for p in c.get("pages", [])))
    bboxes = [b for c in group for b in c.get("bboxes", [])]

    first_b_id = block_ids[0] if block_ids else 0
    last_b_id = block_ids[-1] if block_ids else 0
    doc_id = first["doc_id"]
    chunk_id_raw = f"{doc_id}:{first_b_id}:{last_b_id}:0:{parser_version}"
    chunk_id = hashlib.sha1(chunk_id_raw.encode("utf-8")).hexdigest()

    merged = dict(first)
    merged.update({
        "chunk_id": chunk_id,
        "text": merged_text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "legal_path": merged_legal_path,
        "parent_legal_path": parent,
        "page_sizes": first.get("page_sizes", []),
        "block_ids": block_ids,
        "pages": pages,
        "bboxes": bboxes,
        "char_count": len(merged_text),
        "content_hash": hashlib.sha1(merged_text.encode("utf-8")).hexdigest(),
        "has_contacts": any(c.get("has_contacts", False) for c in group) or check_contacts(merged_text),
        "ord": first.get("ord", 0),
    })
    merged["lines"] = extract_chunk_lines(merged)
    return merged


def merge_short_legal_items(chunks: list[dict], parser_version: str = "2") -> list[dict]:
    """Merges consecutive short legal items (< 300 chars) with same parent in legal_path."""
    if not chunks:
        return []

    result = []
    i = 0
    while i < len(chunks):
        c = chunks[i]
        c_lp = c.get("legal_path") or []
        if (
            not c.get("is_table")
            and len(c.get("text", "")) < 300
            and bool(c_lp)
            and SUB_ARTICLE_PAT.match(c_lp[-1])
        ):
            group = [c]
            cur_len = len(c.get("text", ""))
            parent = c_lp[:-1]
            last_elem = c_lp[-1]

            j = i + 1
            while j < len(chunks):
                nxt = chunks[j]
                nxt_lp = nxt.get("legal_path") or []
                if (
                    not nxt.get("is_table")
                    and nxt.get("doc_id") == c.get("doc_id")
                    and len(nxt.get("text", "")) < 300
                    and bool(nxt_lp)
                    and nxt_lp[:-1] == parent
                    and are_compatible_legal_items(last_elem, nxt_lp[-1])
                    and nxt.get("section") == c.get("section")
                    and nxt.get("lang") == c.get("lang")
                    and cur_len + 1 + len(nxt.get("text", "")) <= MAX_MERGE_CHARS
                ):
                    group.append(nxt)
                    cur_len += 1 + len(nxt.get("text", ""))
                    j += 1
                else:
                    break

            if len(group) > 1:
                result.append(merge_legal_group(group, parser_version))
                i = j
            else:
                result.append(c)
                i += 1
        else:
            result.append(c)
            i += 1

    return result


def postprocess_chunks(chunks: list[dict], parser_version: str = "2") -> list[dict]:
    """1. Merges adjacent short legal items (< 300 chars, same parent in legal_path).
    2. Merges chunks < 150 chars with neighbors in same section/article.
    3. Drops isolated tails < 30 chars.
    """
    if not chunks:
        return []

    # 1. Merge adjacent short legal items (< 300 chars, same parent in legal_path)
    chunks = merge_short_legal_items(chunks, parser_version=parser_version)

    # 1. Merge chunks < 150 chars with neighbor in same section / legal_path / lang
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(chunks):
            c = chunks[i]
            if not c.get("is_table") and len(c.get("text", "")) < 150:
                merged = False
                # Try previous neighbor first
                if i > 0:
                    prev = chunks[i - 1]
                    if (
                        not prev.get("is_table")
                        and prev.get("doc_id") == c.get("doc_id")
                        and prev.get("section") == c.get("section")
                        and prev.get("legal_path") == c.get("legal_path")
                        and prev.get("lang") == c.get("lang")
                        and len(prev.get("text", "")) + len(c.get("text", "")) + 2 <= MAX_MERGE_CHARS
                    ):
                        chunks[i - 1] = merge_two_chunks(prev, c, parser_version)
                        chunks.pop(i)
                        changed = True
                        merged = True
                # If not merged with previous, try next neighbor
                if not merged and i + 1 < len(chunks):
                    nxt = chunks[i + 1]
                    if (
                        not nxt.get("is_table")
                        and nxt.get("doc_id") == c.get("doc_id")
                        and nxt.get("section") == c.get("section")
                        and nxt.get("legal_path") == c.get("legal_path")
                        and nxt.get("lang") == c.get("lang")
                        and len(nxt.get("text", "")) + len(c.get("text", "")) + 2 <= MAX_MERGE_CHARS
                    ):
                        chunks[i] = merge_two_chunks(c, nxt, parser_version)
                        chunks.pop(i + 1)
                        changed = True
                        merged = True
                if not merged:
                    i += 1
            else:
                i += 1

    # 2. Discard isolated tails < 30 chars
    filtered = []
    for c in chunks:
        text = c.get("text", "").strip()
        if len(text) < 30 and not c.get("is_table"):
            continue
        filtered.append(c)

    return filtered




HEADING_TYPES = ("heading", "title", "section_header")


def is_heading(block: dict) -> bool:
    return block.get("type") in HEADING_TYPES


def starts_new_group(group: list[dict], group_len: int, block: dict) -> bool:
    """Whether `block` must start a new chunk instead of joining `group`.

    Headings attach to the content that follows them; a group closes when a new
    heading follows content, or on a change of section, legal path or language,
    or when the group would exceed MAX_MERGE_CHARS.
    """
    if not group:
        return False
    prev = group[-1]
    has_content = any(not is_heading(b) for b in group)

    if is_heading(block):
        if has_content:
            return True
        # Only headings so far: keep nesting sub-headings, close on a sibling/parent heading.
        parent = (*prev.get("section", []), prev.get("text", "").strip())
        section = tuple(block.get("section", []))
        return not (section and section[: len(parent)] == parent)

    lang_changed = bool(prev.get("lang") and block.get("lang") and prev["lang"] != block["lang"])
    too_long = group_len + len(block.get("text", "").strip()) + 2 > MAX_MERGE_CHARS
    if not has_content:
        return lang_changed or too_long
    return (lang_changed or too_long
            or tuple(prev.get("legal_path", [])) != tuple(block.get("legal_path", []))
            or tuple(prev.get("section", [])) != tuple(block.get("section", [])))


def chunk_document(doc: dict, parser_version: str = "2") -> list[dict]:
    """Chunks either a parsed file document or a parsed page document."""
    kind = doc.get("kind", "file")
    raw_blocks = doc.get("blocks", [])
    if not raw_blocks:
        return []

    # Metadata extraction
    if kind == "page":
        doc_id = doc.get("doc_id") or doc.get("id") or f"page:{doc.get('url_key') or url_key(doc.get('url') or '')}"
        meta = {
            "title": doc.get("title") or "",
            "doc_type": "page",
            "number": None,
            "date": doc.get("date"),
            "category": doc.get("category") or "",
            "site": doc.get("site") or "",
            "url": doc.get("url") or "",
            "found_on": doc.get("url") or "",
            "lang": normalize_lang(doc.get("lang")),
            "sha256": doc.get("html_hash") or doc.get("sha256"),
            "previous_sha256": doc.get("previous_sha256"),
            "version": doc.get("version", 1),
            "updated_at": doc.get("updated_at"),
        }
        allow_sub = is_act_or_has_major_legal(meta, raw_blocks)
        tracker = LegalHierarchyTracker(allow_sub_articles=allow_sub)
        blocks = []
        for b in raw_blocks:
            b_copy = dict(b)
            tracker.process_block(b_copy)
            blocks.append(b_copy)
    else:
        sha256 = doc.get("sha256") or ""
        sources = doc.get("sources", [])
        src0 = sources[0] if sources else {}
        primary_url = doc.get("primary_url") or (src0.get("url") if src0 else "") or doc.get("url") or ""
        if doc.get("doc_id"):
            doc_id = doc["doc_id"]
        elif primary_url:
            doc_id = f"file:{url_key(primary_url)}"
        else:
            doc_id = f"file:{sha256}"

        doc_meta = doc.get("metadata", {})
        meta = {
            "title": doc_meta.get("title") or "",
            "doc_type": doc_meta.get("doc_type"),
            "number": doc_meta.get("number"),
            "date": doc_meta.get("date"),
            "category": src0.get("category") or "",
            "site": src0.get("site") or "",
            "url": primary_url or (src0.get("url") or ""),
            "found_on": src0.get("found_on") or "",
            "lang": normalize_lang(doc_meta.get("lang")),
            "sha256": sha256,
            "previous_sha256": doc.get("previous_sha256"),
            "version": doc.get("version", 1),
            "updated_at": doc.get("updated_at"),
        }

        # Apply legal hierarchy tracker to assign legal_path to every block
        allow_sub = is_act_or_has_major_legal(doc_meta, raw_blocks)
        tracker = LegalHierarchyTracker(allow_sub_articles=allow_sub)
        blocks = []
        for b in raw_blocks:
            b_copy = dict(b)
            tracker.process_block(b_copy)
            blocks.append(b_copy)

    # Extract page_sizes for documents (used for citation overlay and online part)
    raw_pages = doc.get("pages", [])
    page_sizes = [
        {"n": p.get("n"), "width": p.get("width"), "height": p.get("height")}
        for p in raw_pages if p.get("width") and p.get("height")
    ]

    chunks: list[dict] = []
    cur_group: list[dict] = []
    cur_len = 0

    def flush_group():
        nonlocal cur_group, cur_len
        if not cur_group:
            return
        combined_text = "\n\n".join(b["text"] for b in cur_group).strip()
        if not combined_text:
            cur_group = []
            cur_len = 0
            return

        if len(combined_text) > MAX_BLOCK_CHARS:
            parts = split_long_text(combined_text, MAX_MERGE_CHARS, MAX_BLOCK_CHARS, OVERLAP_CHARS)
            for p_idx, part in enumerate(parts):
                chunks.append(build_chunk(
                    doc_id=doc_id,
                    kind=kind,
                    text=part,
                    blocks=cur_group,
                    meta=meta,
                    parser_version=parser_version,
                    part_idx=p_idx,
                    is_table=False,
                    page_sizes=page_sizes,
                ))
        else:
            chunks.append(build_chunk(
                doc_id=doc_id,
                kind=kind,
                text=combined_text,
                blocks=cur_group,
                meta=meta,
                parser_version=parser_version,
                part_idx=0,
                is_table=False,
                page_sizes=page_sizes,
            ))
        cur_group = []
        cur_len = 0

    for block in blocks:
        b_type = block.get("type")
        b_text = block.get("text", "").strip()
        if not b_text and b_type == "table" and (block.get("header") or block.get("rows")):
            b_text = format_table_markdown(block.get("header") or [], block.get("rows") or [])
            block["text"] = b_text
        if not b_text:
            continue

        # Tables are always separate chunks
        if b_type == "table":
            flush_group()
            tbl_parts = chunk_table_block(block, MAX_MERGE_CHARS)
            for p_idx, tbl_text in enumerate(tbl_parts):
                chunks.append(build_chunk(
                    doc_id=doc_id,
                    kind=kind,
                    text=tbl_text,
                    blocks=[block],
                    meta=meta,
                    parser_version=parser_version,
                    part_idx=p_idx,
                    is_table=True,
                    page_sizes=page_sizes,
                ))
            continue

        if starts_new_group(cur_group, cur_len, block):
            flush_group()

        cur_group.append(block)
        cur_len += len(b_text) + 2

    flush_group()
    return postprocess_chunks(chunks, parser_version=parser_version)

