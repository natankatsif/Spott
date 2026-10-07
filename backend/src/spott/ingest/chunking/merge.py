"""After chunking: short neighbours merged (legal items of one parent into a range, "pct. 3-5"; a short chunk into
its neighbour of the same section), isolated tails dropped."""

import hashlib
import re

from spott.ingest.common.text import check_contacts

from .labels import chunk_id, make_citation_label
from .limits import MAX_MERGE_CHARS
from .lines import extract_chunk_lines


def merge_two_chunks(a: dict, b: dict) -> dict:
    """Merges two adjacent chunks from the same document and section."""
    merged_text = (a["text"] + "\n\n" + b["text"]).strip()
    block_ids = list(dict.fromkeys(a.get("block_ids", []) + b.get("block_ids", [])))
    pages = sorted(set(a.get("pages", []) + b.get("pages", [])))
    bboxes = a.get("bboxes", []) + b.get("bboxes", [])
    has_contacts = a.get("has_contacts", False) or b.get("has_contacts", False)

    first_block_id = block_ids[0] if block_ids else 0
    last_block_id = block_ids[-1] if block_ids else 0
    doc_id = a["doc_id"]
    merged_id = chunk_id(doc_id, first_block_id, last_block_id)

    citation_label = a.get("citation_label") or b.get("citation_label") or ""
    title = a.get("title") or b.get("title") or ""
    embed_text = f"{title}\n{citation_label}\n{merged_text}"

    merged = dict(a)
    merged.update({
        "chunk_id": merged_id,
        "text": merged_text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "page_sizes": a.get("page_sizes", []),
        "block_ids": block_ids,
        "pages": pages,
        "bboxes": bboxes,
        "content_hash": hashlib.sha1(merged_text.encode("utf-8")).hexdigest(),
        "has_contacts": has_contacts,
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


def merge_legal_group(group: list[dict]) -> dict:
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
    merged_id = chunk_id(doc_id, first_b_id, last_b_id)

    merged = dict(first)
    merged.update({
        "chunk_id": merged_id,
        "text": merged_text,
        "embed_text": embed_text,
        "citation_label": citation_label,
        "legal_path": merged_legal_path,
        "page_sizes": first.get("page_sizes", []),
        "block_ids": block_ids,
        "pages": pages,
        "bboxes": bboxes,
        "content_hash": hashlib.sha1(merged_text.encode("utf-8")).hexdigest(),
        "has_contacts": any(c.get("has_contacts", False) for c in group) or check_contacts(merged_text),
    })
    merged["lines"] = extract_chunk_lines(merged)
    return merged


def merge_short_legal_items(chunks: list[dict]) -> list[dict]:
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
                result.append(merge_legal_group(group))
                i = j
            else:
                result.append(c)
                i += 1
        else:
            result.append(c)
            i += 1

    return result


def postprocess_chunks(chunks: list[dict]) -> list[dict]:
    """1. Merges adjacent short legal items (< 300 chars, same parent in legal_path).
    2. Merges chunks < 150 chars with neighbors in same section/article.
    3. Drops isolated tails < 30 chars.
    """
    if not chunks:
        return []

    # 1. Merge adjacent short legal items (< 300 chars, same parent in legal_path)
    chunks = merge_short_legal_items(chunks)

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
                        chunks[i - 1] = merge_two_chunks(prev, c)
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
                        chunks[i] = merge_two_chunks(c, nxt)
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
