"""The prompt's sources: each chunk's numbered lines around what the search matched, within a character budget."""

import os
from dataclasses import dataclass

from ..schemas import AskRequest
from .chunks import document_title, quote_lang, recency
from .corpus import Store, add_continuations
from .prompts import conversation, today_line

MAX_SOURCES = 20  # after continuations, amending acts and the freshness queries
MAX_LINES_PER_CHUNK = 40
CONTEXT_LINES = 5  # lines shown around a matched line of one of the first WIDE_SOURCES chunks
WIDE_SOURCES = 4
NARROW_CONTEXT_LINES = 2  # around a matched line of the other chunks
MAX_HITS = 2  # matched lines per chunk that get context: the best ones
SOURCE_BUDGET_CHARS = int(os.getenv("SOURCE_BUDGET_CHARS", "6500"))  # source lines per prompt (~2,100 tokens)


@dataclass
class Source:
    ref: str  # "S1"
    chunk: dict
    lines: list[tuple[int, dict]]  # the lines shown: (number in the chunk, {line_id, idx, text, page, bboxes})


def window(numbered: list[tuple[int, dict]], matched: list[str], radius: int = CONTEXT_LINES
           ) -> list[tuple[int, dict]]:
    """The best MAX_HITS matched lines with `radius` lines around each; the chunk's first lines when none matched."""
    place = {line.get("line_id"): n for n, line in numbered}
    hits = [place[lid] for lid in matched if lid in place][:MAX_HITS] or [1]
    return [(n, line) for n, line in numbered if any(abs(n - h) <= radius for h in hits)]


def build_sources(chunks: list[dict], lines_by_chunk: dict[str, list[dict]],
                  focus: dict[str, list[str]] | None = None, budget: int | None = None,
                  by_date: bool = False) -> list[Source]:
    """Numbered lines per chunk, chunks in priority order. Only the lines around what the search matched go to
    the model (a list that continues an introduction goes whole; chunks past the first WIDE_SOURCES with less
    context); a line already shown in an earlier source, or with no letters (OCR noise
    of table rules), is left out. Chunks are taken while their lines fit the character budget, then ordered
    newest first if `by_date`. Line numbers stay the line's place in the chunk."""
    picked, seen, used = [], set(), 0
    for chunk in chunks:
        lines = lines_by_chunk.get(chunk["chunk_id"])
        if not lines:  # chunk without a line index: fall back to its text lines
            lines = [{"line_id": None, "text": t.strip(), "page": None, "bboxes": []}
                     for t in chunk.get("text", "").split("\n") if t.strip()]
        numbered = list(enumerate(lines, 1))
        if not chunk.get("continuation"):
            wide = len(picked) < WIDE_SOURCES
            numbered = window(numbered, (focus or {}).get(chunk["chunk_id"], []),
                              CONTEXT_LINES if wide else NARROW_CONTEXT_LINES)
        shown, keys = [], set()
        for n, line in numbered[:MAX_LINES_PER_CHUNK]:
            key = " ".join(line["text"].split()).casefold()
            if key in seen or key in keys or not any(ch.isalpha() for ch in key):
                continue
            keys.add(key)
            shown.append((n, line))
        size = sum(len(line["text"]) for _, line in shown)
        if not shown or (budget is not None and picked and used + size > budget):
            continue  # a smaller chunk further down may still fit
        seen |= keys
        used += size
        picked.append((chunk, shown))
    if by_date:
        dated = sorted((p for p in picked if recency(p[0])), key=lambda p: recency(p[0]), reverse=True)
        picked = dated + [p for p in picked if not recency(p[0])]
    return [Source(ref=f"S{i}", chunk=chunk, lines=shown) for i, (chunk, shown) in enumerate(picked, 1)]


def prepare_sources(chunks: list[dict], store: Store, focus: dict[str, list[str]] | None = None,
                    by_date: bool = False) -> tuple[list[dict], list[Source], dict[str, dict]]:
    """Chunks in priority order → the prompt's sources, within the character budget."""
    chunks = add_continuations(chunks, store)[:MAX_SOURCES]
    sources = build_sources(chunks, store.lines([c["chunk_id"] for c in chunks]), focus, SOURCE_BUDGET_CHARS,
                            by_date)
    used = {s.chunk["chunk_id"] for s in sources}
    chunks = [c for c in chunks if c["chunk_id"] in used]
    return chunks, sources, store.documents(list({c["doc_id"] for c in chunks}))


def render_prompt(req: AskRequest, sources: list[Source]) -> str:
    blocks = []
    for s in sources:
        c = s.chunk
        # Only what isn't in the title already; Romanian is the default language, a web page shows its site.
        facts = {"date": c.get("date"), "undated, mentions dates up to": None if c.get("date") else c.get("mentions_until"),
                 "point": " › ".join(c.get("legal_path") or []) or None,
                 "web page on": c.get("site") if c.get("kind") != "file" else None,
                 "language": c.get("lang") if quote_lang(c) != "ro" else None}
        header = f"[{s.ref}] {document_title(c)}\n" + " | ".join(f"{k}: {v}" for k, v in facts.items() if v)
        body, previous = [], None
        for n, line in s.lines:
            if previous is not None and n != previous + 1:
                body.append("…")
            body.append(f"{s.ref}.L{n}: {line['text']}")
            previous = n
        blocks.append(header + "\n" + "\n".join(body))
    return f"{today_line()}{conversation(req, 500)}Question: {req.question}\n\nSources:\n\n" + "\n\n".join(blocks)
