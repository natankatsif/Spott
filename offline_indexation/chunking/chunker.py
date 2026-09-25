"""Document and page chunker.

Transforms parsed files (data/parsed/*.json) and parsed pages (data/parsed/pages/*.json)
into chunks respecting legal hierarchy, section headers, tables, language boundaries,
and length constraints.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .legal import LegalHierarchyTracker

MAX_MERGE_CHARS = 1500
MAX_BLOCK_CHARS = 2500
OVERLAP_CHARS = 200

PHONE_RE = re.compile(r"(?:(?:\+373|0)\s*\(?\d{2,3}\)?[\s.-]*\d{2,3}[\s.-]*\d{2,4})")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def check_contacts(text: str) -> bool:
    return bool(PHONE_RE.search(text) or EMAIL_RE.search(text))


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


def format_table_markdown(header: list[str], rows: list[list[str]]) -> str:
    cols = max(len(header), max((len(r) for r in rows), default=0))
    if cols == 0:
        return ""
    h = header + [""] * (cols - len(header))
    lines = [
        "| " + " | ".join(h) + " |",
        "| " + " | ".join(["---"] * cols) + " |",
    ]
    for r in rows:
        padded = r + [""] * (cols - len(r))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


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
) -> dict:
    first_block_id = blocks[0]["id"] if blocks else 0
    last_block_id = blocks[-1]["id"] if blocks else 0

    chunk_id_raw = f"{doc_id}:{first_block_id}:{last_block_id}:{part_idx}:{parser_version}"
    chunk_id = hashlib.sha1(chunk_id_raw.encode("utf-8")).hexdigest()

    section = override_section if override_section is not None else (blocks[0].get("section", []) if blocks else [])
    legal_path = override_legal_path if override_legal_path is not None else (blocks[0].get("legal_path", []) if blocks else [])

    citation_label = make_citation_label(meta, kind, legal_path, section)
    title = meta.get("title") or ""
    embed_text = f"{title}\n{citation_label}\n{text}"

    block_ids = [b["id"] for b in blocks if "id" in b]
    pages = sorted({b["page"] for b in blocks if b.get("page") is not None})
    bboxes = [bbox for b in blocks for bbox in b.get("bboxes", [])]
    lang = blocks[0].get("lang") or meta.get("lang") or "ro"

    has_contacts = any(b.get("has_contacts", False) for b in blocks) or check_contacts(text)

    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "kind": kind,
        "text": text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "section": section,
        "legal_path": legal_path,
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
    }


def chunk_document(doc: dict, parser_version: str = "2") -> list[dict]:
    """Chunks either a parsed file document or a parsed page document."""
    kind = doc.get("kind", "file")
    raw_blocks = doc.get("blocks", [])
    if not raw_blocks:
        return []

    # Metadata extraction
    if kind == "page":
        doc_id = doc.get("doc_id") or doc.get("id") or f"page:{doc.get('url', '')}"
        meta = {
            "title": doc.get("title") or "",
            "doc_type": None,
            "number": None,
            "date": None,
            "category": doc.get("category") or "",
            "site": doc.get("site") or "",
            "url": doc.get("url") or "",
            "found_on": doc.get("url") or "",
            "lang": doc.get("lang") or "ro",
        }
        blocks = raw_blocks
    else:
        sha256 = doc.get("sha256") or ""
        doc_id = f"file:{sha256}"
        sources = doc.get("sources", [])
        src0 = sources[0] if sources else {}
        doc_meta = doc.get("metadata", {})
        meta = {
            "title": doc_meta.get("title") or "",
            "doc_type": doc_meta.get("doc_type"),
            "number": doc_meta.get("number"),
            "date": doc_meta.get("date"),
            "category": src0.get("category") or "",
            "site": src0.get("site") or "",
            "url": src0.get("url") or "",
            "found_on": src0.get("found_on") or "",
            "lang": doc_meta.get("lang") or "ro",
        }

        # Apply legal hierarchy tracker to assign legal_path to every block
        tracker = LegalHierarchyTracker()
        blocks = []
        for b in raw_blocks:
            b_copy = dict(b)
            tracker.process_block(b_copy)
            blocks.append(b_copy)

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
            ))
        cur_group = []
        cur_len = 0

    for block in blocks:
        b_type = block.get("type")
        b_text = block.get("text", "").strip()
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
                ))
            continue

        # Check boundary rules with current group
        if cur_group:
            prev = cur_group[-1]
            prev_legal = tuple(prev.get("legal_path", []))
            curr_legal = tuple(block.get("legal_path", []))
            prev_sec = tuple(prev.get("section", []))
            curr_sec = tuple(block.get("section", []))
            prev_lang = prev.get("lang")
            curr_lang = block.get("lang")

            # Boundary: change in legal path, change in section, change in language, or merge length limit
            boundary = (
                prev_legal != curr_legal or
                prev_sec != curr_sec or
                (prev_lang and curr_lang and prev_lang != curr_lang) or
                (cur_len + len(b_text) + 2 > MAX_MERGE_CHARS)
            )
            if boundary:
                flush_group()

        cur_group.append(block)
        cur_len += len(b_text) + 2

    flush_group()
    return chunks
