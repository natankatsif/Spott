"""What the answer reads from the index besides the search results (Store; api/store.PgStore in the app), and the
chunk lists completed through it."""

from typing import Protocol

from .chunks import latest_date, position


class Store(Protocol):
    def chunk_meta(self, chunk_ids: list[str]) -> dict[str, dict]: ...
    def next_chunks(self, anchors: list[tuple[str, int]]) -> list[dict]: ...
    def lines(self, chunk_ids: list[str]) -> dict[str, list[dict]]: ...
    def documents(self, doc_ids: list[str]) -> dict[str, dict]: ...
    def later_acts(self, patterns: list[str], exclude_doc_ids: list[str], limit: int = 20) -> list[dict]: ...
    def grep_lines(self, keywords: list[str], limit: int = 200) -> list[dict]: ...
    def dated_lines(self, doc_ids: list[str]) -> dict[str, list[str]]: ...
    def contacts_near(self, question: str, limit: int = 8) -> tuple[list[dict], dict | None]: ...


def add_continuations(chunks: list[dict], store: Store) -> list[dict]:
    """A chunk ending with ':' introduces a list or table that went into the next chunk
    ("Se constituie Grupul … în următoarea componență:"): retrieval finds the intro, the answer is below it."""
    anchors = [(c["doc_id"], position(c)) for c in chunks
               if position(c) is not None and (c.get("text") or "").rstrip().endswith(":")]
    if not anchors:
        return chunks
    following = {(n["doc_id"], n["anchor_pos"]): n for n in store.next_chunks(anchors)}
    seen = {c["chunk_id"] for c in chunks}
    out = []
    for c in chunks:
        out.append(c)
        n = following.get((c["doc_id"], position(c)))
        if n and n["chunk_id"] not in seen:
            seen.add(n["chunk_id"])
            out.append(n | {"continuation": True})
    return out


def with_mentioned_dates(chunks: list[dict], store: Store) -> list[dict]:
    undated = list({c["doc_id"] for c in chunks if not c.get("date")})
    lines = store.dated_lines(undated) if undated else {}
    until = {doc_id: latest_date(texts) for doc_id, texts in lines.items()}
    return [c | {"mentions_until": until[c["doc_id"]]} if until.get(c["doc_id"]) else c for c in chunks]


def complete(items: list[dict], store: Store) -> list[dict]:
    """Retrieval rows completed with all chunk fields from the store (dates, act numbers, points, boxes)."""
    ids = [c["chunk_id"] for c in items]
    meta = store.chunk_meta(ids) if ids else {}
    return [c | meta.get(c["chunk_id"], {}) for c in items if "doc_id" in c or c["chunk_id"] in meta]
