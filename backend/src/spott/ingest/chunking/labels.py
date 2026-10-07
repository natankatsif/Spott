"""What identifies and names a chunk: its id (stable while the document's blocks don't change) and its citation label
("Decizie nr. 12/14 din 2020-07-28 › pct. 5")."""

import hashlib

# Part of every chunk id: a change of how documents are chunked can give all chunks new ids.
CHUNK_ID_VERSION = "2"


def chunk_id(doc_id: str, first_block_id, last_block_id, part_idx: int = 0) -> str:
    return hashlib.sha1(f"{doc_id}:{first_block_id}:{last_block_id}:{part_idx}:{CHUNK_ID_VERSION}".encode()).hexdigest()


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
