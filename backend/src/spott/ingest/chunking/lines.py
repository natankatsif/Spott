"""A chunk's lines: what the answer cites and the line search finds. A table chunk has a line per row (with the
column names, for the embedding); other text a line per sentence or so, never shorter than 25 characters; each line
with its page and boxes from the block it came from."""

import hashlib
import re


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
    pages = chunk.get("pages", [])
    default_page = pages[0] if (pages and isinstance(pages, list)) else None
    default_bboxes = chunk.get("bboxes", [])
    lang = chunk.get("lang")

    lines_data: list[tuple[str, str, int | None, list]] = []

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
            lines_data.append((r, embed_text, default_page, default_bboxes))
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
            m_page = default_page
            m_bboxes = default_bboxes
            for b in raw_blocks:
                b_txt = b.get("text", "")
                if line_str in b_txt or (b_txt and b_txt in line_str):
                    m_page = b.get("page", default_page)
                    m_bboxes = b.get("bboxes", default_bboxes)
                    break

            embed_text = f"{prefix}\n{line_str}" if prefix else line_str
            lines_data.append((line_str, embed_text, m_page, m_bboxes))

    result = []
    for idx, (l_text, l_embed, pg, bbox) in enumerate(lines_data):
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
            "page": pg,
            "bboxes": bbox or [],
            "content_hash": c_hash,
        })

    return result
