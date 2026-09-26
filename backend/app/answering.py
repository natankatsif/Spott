"""Answering a question from the corpus: retrieve → LLM over numbered lines → citations checked by code.

The model never writes quotes. It returns ids of source lines ("S2.L4") for every sentence; the quoted text
is then taken from the index. A sentence without a valid line id is dropped, so the answer can't carry a
claim that no source line backs.
"""

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from retrieval import RERANKER_ENABLED, retrieve
from retrieval.links import make_deep_link
from retrieval.pipeline import acquire_conn

from .llm import LLM, LLMResult
from .schemas import AskRequest, AskResponse, Citation, Conflict, NavLink

log = logging.getLogger("backend.answering")

TOP_CHUNKS = 8
MAX_LINES_PER_CHUNK = 40
MAX_NAV_LINKS = 3
QUERY_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "query_logs"

LANGUAGE_NAMES = {"ro": "Romanian", "ru": "Russian"}
NOT_FOUND = {
    "ro": "Informația nu a fost găsită în documentele publice ale Primăriei disponibile asistentului.",
    "ru": "В публичных документах Примэрии, доступных ассистенту, нет информации по этому вопросу.",
}
OUT_OF_SCOPE = {
    "ro": "Pot răspunde doar la întrebări despre Primăria Chișinău, serviciile și documentele ei.",
    "ru": "Я отвечаю только на вопросы о Примэрии Кишинэу, её услугах и документах.",
}

SYSTEM_PROMPT = """\
You answer questions from citizens and employees of the Chișinău City Hall using ONLY the numbered source \
lines given in the user message.

Rules:
- Use only what the source lines state. No outside knowledge, no assumptions, no advice the sources don't give.
- Write the answer in {language}, as short plain sentences. For every sentence list in "refs" the ids of the \
lines that state it (e.g. "S2.L4"), copied exactly. Never write a sentence you can't back with a line. \
Don't put line ids into the sentence text.
- Use a number, price, date or name only if the same line (or the same table row) says what it refers to. \
Never pair values with labels by their order across separate lines.
- verdict:
  - "answered": the lines answer the question;
  - "partial": they answer only part of it — answer that part and list the unanswered parts in "missing", \
in {language};
  - "not_found": the lines don't answer the question (a related topic is not an answer) — no sentences;
  - "out_of_scope": the question isn't about the city, its institutions, services or documents (weather, \
general knowledge, chit-chat), even if some lines look loosely related — no sentences.
- conflict: if sources give different values for the same thing (fee, deadline, requirement, address, \
schedule), fill it with the parameter, the refs of every side, their values, and resolution "newer" when a \
later act clearly replaces the earlier one (then answer by the newer act and mention the older one), \
otherwise "unclear" (then present both sides). Otherwise null.
- translations: for every line you cite whose language isn't {language}, give its translation into {language}.
"""

_STRINGS = {"type": "array", "items": {"type": "string"}}
ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "sentences", "missing", "conflict", "translations"],
    "properties": {
        "verdict": {"type": "string", "enum": ["answered", "partial", "not_found", "out_of_scope"]},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "refs"],
                "properties": {"text": {"type": "string"}, "refs": _STRINGS},
            },
        },
        "missing": _STRINGS,
        "conflict": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["param", "refs", "values", "resolution"],
                    "properties": {
                        "param": {"type": "string"},
                        "refs": _STRINGS,
                        "values": _STRINGS,
                        "resolution": {"type": "string", "enum": ["newer", "unclear"]},
                    },
                },
            ]
        },
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "text"],
                "properties": {"ref": {"type": "string"}, "text": {"type": "string"}},
            },
        },
    },
}


@dataclass
class Source:
    ref: str  # "S1"
    chunk: dict
    lines: list[dict]  # line_id, idx, text, page


def detect_lang(text: str) -> str | None:
    """'ru' if Cyrillic letters dominate, 'ro' if Latin, None if there are no letters."""
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isalpha()) - cyr
    if not cyr and not lat:
        return None
    return "ru" if cyr > lat else "ro"


CHUNK_COLUMNS = (
    "chunk_id, doc_id, kind, lang, url, found_on, site, citation_label, text, "
    "title, doc_type, number, date, legal_path, has_contacts, pages, block_ids"
)
# Position of a chunk in its document: its first block. (chunks.ord is not filled by the indexer.)
POSITION = "(c.block_ids->>0)::int"


def load_chunk_meta(pool, chunk_ids: list[str]) -> dict[str, dict]:
    """Citation fields that retrieve() doesn't return: act number and date, point path, contacts flag."""
    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT chunk_id, title, doc_type, number, date, legal_path, has_contacts, kind, pages, block_ids "
            "FROM chunks WHERE chunk_id = ANY(%s)",
            (chunk_ids,),
        )
        return {row["chunk_id"]: row for row in cur.fetchall()}


def position(chunk: dict) -> int | None:
    blocks = chunk.get("block_ids")
    return blocks[0] if blocks else None


def load_next_chunks(pool, anchors: list[tuple[str, int]]) -> list[dict]:
    """The chunk right after each (doc_id, position) in its document; anchor_pos tells which one it follows."""
    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT n.*, t.pos AS anchor_pos
            FROM unnest(%s::text[], %s::int[]) AS t(doc_id, pos)
            CROSS JOIN LATERAL (
                SELECT {CHUNK_COLUMNS} FROM chunks c
                WHERE c.doc_id = t.doc_id AND jsonb_array_length(c.block_ids) > 0 AND {POSITION} > t.pos
                ORDER BY {POSITION} LIMIT 1
            ) n
            """,
            ([d for d, _ in anchors], [o for _, o in anchors]),
        )
        return cur.fetchall()


def add_continuations(pool, chunks: list[dict], next_fn: Callable) -> list[dict]:
    """A chunk ending with ':' introduces a list or table that went into the next chunk
    ("Se constituie Grupul … în următoarea componență:") — retrieval finds the intro, the answer is below it."""
    anchors = [(c["doc_id"], position(c)) for c in chunks
               if position(c) is not None and (c.get("text") or "").rstrip().endswith(":")]
    if not anchors:
        return chunks
    following = {(n["doc_id"], n["anchor_pos"]): n for n in next_fn(pool, anchors)}
    seen = {c["chunk_id"] for c in chunks}
    out = []
    for c in chunks:
        out.append(c)
        n = following.get((c["doc_id"], position(c)))
        if n and n["chunk_id"] not in seen:
            seen.add(n["chunk_id"])
            out.append(n)
    return out


def load_lines(pool, chunk_ids: list[str]) -> dict[str, list[dict]]:
    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT line_id, chunk_id, idx, text, page FROM lines WHERE chunk_id = ANY(%s) ORDER BY chunk_id, idx",
            (chunk_ids,),
        )
        by_chunk: dict[str, list[dict]] = {}
        for row in cur.fetchall():
            by_chunk.setdefault(row["chunk_id"], []).append(row)
    return by_chunk


def build_sources(chunks: list[dict], lines_by_chunk: dict[str, list[dict]]) -> list[Source]:
    sources = []
    for i, chunk in enumerate(chunks, 1):
        lines = lines_by_chunk.get(chunk["chunk_id"])
        if not lines:  # chunk without a line index: fall back to its text lines
            lines = [{"line_id": None, "text": t.strip(), "page": None}
                     for t in chunk.get("text", "").split("\n") if t.strip()]
        sources.append(Source(ref=f"S{i}", chunk=chunk, lines=lines[:MAX_LINES_PER_CHUNK]))
    return sources


def doc_label(chunk: dict) -> str:
    """'Decizie nr. 12/14 din 2020-07-28' for acts, the document title otherwise."""
    if chunk.get("doc_type") and chunk.get("number"):
        label = f"{chunk['doc_type'].capitalize()} nr. {chunk['number']}"
        return f"{label} din {chunk['date']}" if chunk.get("date") else label
    return chunk.get("title") or chunk.get("citation_label") or chunk.get("site") or chunk.get("url") or ""


def render_sources(sources: list[Source]) -> str:
    blocks = []
    for s in sources:
        c = s.chunk
        meta = {"document": c.get("title"), "type": c.get("doc_type"), "number": c.get("number"),
                "date": c.get("date"), "site": c.get("site"), "language": c.get("lang")}
        header = f"[{s.ref}] {c.get('citation_label') or doc_label(c)}\n" + " | ".join(
            f"{k}: {v}" for k, v in meta.items() if v)
        body = "\n".join(f"{s.ref}.L{i}: {line['text']}" for i, line in enumerate(s.lines, 1))
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks)


def make_citation(source: Source, line: dict, lang: str, translation: str | None) -> Citation:
    c = source.chunk
    url = c.get("url") or ""
    page = line.get("page") or (c.get("pages") or [None])[0]
    doc_lang = c.get("lang")
    return Citation(
        document_title=doc_label(c),
        url=url,
        passage=line["text"],
        location=" › ".join(c.get("legal_path") or []) or None,
        page=page,
        published=c.get("date"),
        chunk_id=c.get("chunk_id"),
        line_id=line.get("line_id"),
        found_on=c.get("found_on"),
        deep_link=make_deep_link(url, line["text"], page) or None,
        doc_lang=doc_lang,
        passage_translation=translation if doc_lang and doc_lang != lang else None,
    )


def nav_links(chunks: list[dict]) -> list[NavLink]:
    """Site pages to go to: where the cited documents are published (or the cited pages themselves)."""
    links: dict[str, NavLink] = {}
    for c in chunks:
        url = c.get("found_on") or c.get("url")
        if url and url not in links:
            links[url] = NavLink(title=doc_label(c) if c.get("kind") == "page" else c.get("site") or url, url=url)
    return list(links.values())[:MAX_NAV_LINKS]


def not_found_response(lang: str, query_id: str, retrieved: list[dict], gaps: list[str] | None = None) -> AskResponse:
    # Nearest pages with contacts, so the person isn't left at a dead end.
    contacts = [c for c in retrieved if c.get("has_contacts")]
    return AskResponse(status="not_found", lang=lang, answer=NOT_FOUND[lang], query_id=query_id,
                       nav_links=nav_links(contacts), gaps=gaps or [])


def build_answer(data: dict, sources: list[Source], lang: str, query_id: str) -> tuple[AskResponse, int]:
    """Turns the model output into a response. Returns it with the number of sentences dropped as unbacked."""
    if data.get("verdict") == "out_of_scope":
        return AskResponse(status="out_of_scope", lang=lang, answer=OUT_OF_SCOPE[lang], query_id=query_id), 0

    lines_by_ref = {f"{s.ref}.L{i}": (s, line) for s in sources for i, line in enumerate(s.lines, 1)}
    translations = {t["ref"]: t["text"] for t in data.get("translations") or []}
    citations: list[Citation] = []
    cited: dict[str, int] = {}  # ref → 1-based citation index

    def cite(ref: str) -> int:
        if ref not in cited:
            source, line = lines_by_ref[ref]
            citations.append(make_citation(source, line, lang, translations.get(ref)))
            cited[ref] = len(citations)
        return cited[ref]

    sentences, dropped = [], 0
    for s in data.get("sentences") or []:
        refs = [r for r in dict.fromkeys(s.get("refs") or []) if r in lines_by_ref]
        text = (s.get("text") or "").strip()
        if not refs or not text:
            dropped += 1
            continue
        sentences.append(f"{text} " + "".join(f"[{cite(r)}]" for r in refs))

    retrieved = [s.chunk for s in sources]
    missing = [m.strip() for m in data.get("missing") or [] if m.strip()]
    if data.get("verdict") == "not_found" or not sentences:
        return not_found_response(lang, query_id, retrieved, missing), dropped

    conflicts = []
    if (c := data.get("conflict")) and len(refs := [r for r in c["refs"] if r in lines_by_ref]) >= 2:
        conflicts.append(Conflict(param=c["param"], citations=[cite(r) for r in refs],
                                  values=c["values"], resolution=c["resolution"]))

    cited_chunks = [lines_by_ref[r][0].chunk for r in cited]
    return AskResponse(
        status="conflict" if any(x.resolution == "unclear" for x in conflicts) else "answered",
        lang=lang,
        answer=" ".join(sentences),
        citations=citations,
        nav_links=nav_links(cited_chunks),
        query_id=query_id,
        conflicts=conflicts,
        gaps=missing if data.get("verdict") == "partial" else [],
    ), dropped


def log_query(record: dict) -> None:
    """One JSON line per question: for gap analysis, eval and the budget (tokens per question)."""
    try:
        QUERY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = QUERY_LOG_DIR / f"{datetime.now(UTC):%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("query log not written: %s", e)


def answer_question(
    pool,
    llm: LLM,
    req: AskRequest,
    *,
    retrieve_fn: Callable = retrieve,
    lines_fn: Callable = load_lines,
    meta_fn: Callable = load_chunk_meta,
    next_fn: Callable = load_next_chunks,
) -> AskResponse:
    started = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:16]}"
    lang = detect_lang(req.question) or req.lang or "ro"

    # No language filter: a Russian question must find Romanian documents.
    result = retrieve_fn(pool, req.question, k=TOP_CHUNKS, rerank=RERANKER_ENABLED)
    llm_result: LLMResult | None = None
    dropped = 0
    chunk_ids = [c["chunk_id"] for c in result.items]
    meta = meta_fn(pool, chunk_ids) if chunk_ids else {}
    chunks = [c | meta.get(c["chunk_id"], {}) for c in result.items]
    if result.not_found or not chunks:
        response = not_found_response(lang, query_id, chunks)
    else:
        chunks = add_continuations(pool, chunks, next_fn)
        sources = build_sources(chunks, lines_fn(pool, [c["chunk_id"] for c in chunks]))
        user = f"Question: {req.question}\n\nSources:\n\n{render_sources(sources)}"
        llm_result = llm.complete_json(SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[lang]), user,
                                       "answer", ANSWER_SCHEMA)
        response, dropped = build_answer(llm_result.data, sources, lang, query_id)

    log_query({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "query_id": query_id,
        "question": req.question,
        "lang": lang,
        "status": response.status,
        "retrieved": [c["chunk_id"] for c in result.items],
        "cited_lines": [c.line_id for c in response.citations],
        "dropped_sentences": dropped,
        "verdict": llm_result.data.get("verdict") if llm_result else None,
        "model": llm_result.model if llm_result else None,
        "prompt_tokens": llm_result.prompt_tokens if llm_result else 0,
        "completion_tokens": llm_result.completion_tokens if llm_result else 0,
        "retrieval_ms": result.timings_ms.get("total"),
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return response
