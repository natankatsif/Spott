"""API contract shared with the frontend (frontend/src/lib/api.ts mirrors it)."""

from typing import Any, Literal

from pydantic import BaseModel, Field
from retrieval.config import RERANK_TOP_K

Lang = Literal["ro", "ru"]
SearchLang = Literal["ro", "ru", "en", "uk"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    lang: Lang | None = None  # None → detect from the question


class Citation(BaseModel):
    document_title: str
    url: str
    passage: str  # verbatim quote from the document
    location: str | None = None  # e.g. "Anexa 1, pct. 3.2"
    page: int | None = None
    published: str | None = None


class NavLink(BaseModel):
    title: str
    url: str


class AskResponse(BaseModel):
    # answered  — answer grounded in citations
    # not_found — corpus has no information on the question
    # conflict  — sources contradict each other; all of them are cited
    status: Literal["answered", "not_found", "conflict"]
    lang: Lang
    answer: str
    citations: list[Citation] = []
    nav_links: list[NavLink] = []


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

