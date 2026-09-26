"""API contract: docs/API.md. The frontend mirrors these models in frontend/src/lib/api.ts;
tests/test_contract.py validates every frontend mock against them, so drift fails CI.

Response models forbid unknown fields: a field the frontend sends or expects that isn't here
is a contract change, not something to pass through silently.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from retrieval.config import RERANK_TOP_K

Lang = Literal["ro", "ru"]
SearchLang = Literal["ro", "ru", "en", "uk"]
AskStatus = Literal["answered", "partial", "not_found", "conflict", "refused"]
ErrorCode = Literal["validation_error", "not_found", "rate_limited", "unavailable", "not_implemented", "internal"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─────────────── POST /api/ask ───────────────


class ChatTurn(Strict):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class PageContext(Strict):
    url: str | None = None
    title: str | None = None


class AskRequest(Strict):
    question: str = Field(min_length=1, max_length=2000)
    # UI language. The answer follows the language the question is written in; this decides ambiguous cases.
    lang: Lang | None = None
    history: list[ChatTurn] = Field(default=[], max_length=10)  # oldest first
    page_context: PageContext | None = None  # the widget sends the page the user is on
    mode: Literal["auto", "fast", "deep"] = "auto"
    session_id: str | None = Field(default=None, max_length=200)


class BBox(Strict):
    """PDF points, origin top-left."""

    page: int
    l: float  # noqa: E741 — contract field name
    t: float
    r: float
    b: float
    page_width: float
    page_height: float


class Citation(Strict):
    id: str  # "c1", referenced from AnswerSentence.cites
    doc_id: str
    chunk_id: str
    line_ids: list[str]
    kind: Literal["file", "page"]
    document_title: str
    doc_type: str | None
    act_number: str | None
    published: str | None  # ISO date
    location: str | None  # "Anexa 1 › pct. 3.2"
    page: int | None
    quote: str  # verbatim from the index, never written by the model
    quote_lang: SearchLang
    translation: str | None  # required when quote_lang != answer lang
    url: str
    deep_link: str  # url#page=N or url#:~:text=…
    found_on: str | None
    site: str | None
    file_url: str | None  # our PDF copy for the viewer, relative to the API; null for web pages
    bboxes: list[BBox]


class AnswerSentence(Strict):
    text: str
    cites: list[str]  # Citation.id values; empty = meta sentence ("Verificați decizia mai nouă…")


class ConflictInfo(Strict):
    kind: Literal["outdated", "contradiction"]  # outdated: a newer act replaces the older one
    explanation: str
    citation_ids: list[str]
    preferred_citation_id: str | None


class ChecklistStep(Strict):
    text: str
    cites: list[str]


class Checklist(Strict):
    title: str
    steps: list[ChecklistStep]
    documents_needed: list[str]
    fee: str | None
    deadline: str | None


class NavLink(Strict):
    title: str
    url: str
    kind: Literal["page", "service", "contact", "document"]
    selector: str | None = None  # widget: CSS selector to highlight when url is the page the user is on


class TraceStep(Strict):
    tool: Literal["search", "grep", "toc", "open", "verify"]
    input: str
    summary: str
    ms: float


class AnswerMeta(Strict):
    model: str | None
    path: Literal["fast", "agent", "none"]
    latency_ms: float
    verified: bool  # quotes re-read from the index and claims checked against them


class AskResponse(Strict):
    id: str  # answer id for /api/feedback
    status: AskStatus
    lang: Lang
    answer: str  # sentences joined by a space; plain text
    sentences: list[AnswerSentence]
    citations: list[Citation]  # display number [n] = index + 1
    conflict: ConflictInfo | None
    checklist: Checklist | None
    nav_links: list[NavLink]
    followups: list[str]
    trace: list[TraceStep]
    meta: AnswerMeta


# ─────────────── POST /api/feedback ───────────────


class FeedbackRequest(Strict):
    answer_id: str = Field(min_length=1, max_length=200)
    vote: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)
    citation_id: str | None = Field(default=None, max_length=50)


class FeedbackResponse(Strict):
    ok: bool


# ─────────────── errors: body of every non-2xx response ───────────────


class ApiError(Strict):
    error: ErrorCode
    message: str
    retry_after_s: float | None = None


# ─────────────── GET /api/wall ───────────────


class WallItem(Strict):
    id: str
    ts: str
    question: str  # personal data masked as •••, at most 200 characters
    lang: Lang
    status: AskStatus
    verified: bool
    latency_ms: float
    top_source: str | None


class WallResponse(Strict):
    items: list[WallItem]  # newest first
    total_questions: int
    by_status: dict[str, int]


# ─────────────── GET /api/corpus/stats ───────────────


class SiteStats(Strict):
    site: str
    category: str | None
    status: Literal["indexed", "pending", "blocked"]
    pages: int
    documents_found: int
    documents_downloaded: int
    chunks: int
    last_crawled: str | None


class CorpusTotals(Strict):
    sites_total: int
    sites_indexed: int
    pages: int
    documents_found: int
    documents_downloaded: int
    chunks: int
    lines: int
    documents_replaced: int
    documents_removed: int


class CorpusStats(Strict):
    updated_at: str | None
    totals: CorpusTotals
    sites: list[SiteStats]


# ─────────────── POST /api/search, /api/tools/*, GET /health ───────────────


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    lang: SearchLang | None = None
    k: int = Field(default=RERANK_TOP_K, ge=1, le=50)
    rerank: bool = False


class MatchedLine(BaseModel):
    line_id: str
    idx: int
    text: str
    score: float | None = None


class SearchResultItem(BaseModel):
    chunk_id: str
    doc_id: str
    citation_label: str
    text: str
    url: str
    found_on: str | None = None
    site: str | None = None
    lang: str | None = None
    page: int | None = None
    parent_legal_path: list[Any] | None = None
    rerank_score: float | None = None
    vec_rank: int | None = None
    fts_rank: int | None = None
    matched_lines: list[MatchedLine] = []


class SearchTimings(BaseModel):
    embed: float
    vector_sql: float
    fts_sql: float
    rerank: float
    total: float


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    timings_ms: SearchTimings
    not_found: bool = False


class HealthResponse(BaseModel):
    status: str
    device: str
    models_loaded: bool
    chunk_count: int


class ToolSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    lang: str | None = None
    site: str | None = None
    k: int = Field(default=8, ge=1, le=50)


class ToolGrepRequest(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    doc_id: str | None = None
    site: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ToolTocRequest(BaseModel):
    doc_id: str = Field(min_length=1)


class ToolOpenRequest(BaseModel):
    doc_id: str = Field(min_length=1)
    node_id: str | None = None
    chunk_id: str | None = None
    max_lines: int = Field(default=60, ge=1, le=200)
