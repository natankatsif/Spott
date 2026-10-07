"""API contract: docs/API.md. The frontend mirrors these models in frontend/src/lib/api.ts;
tests/test_contract.py validates every frontend mock against them, so drift fails CI.

Response models forbid unknown fields: a field the frontend sends or expects that isn't here
is a contract change, not something to pass through silently.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Lang = Literal["ro", "ru", "en"]  # answers follow the question; the documents are RO/RU
SearchLang = Literal["ro", "ru", "en", "uk"]
AskStatus = Literal["answered", "partial", "not_found", "conflict", "refused"]
ErrorCode = Literal["validation_error", "unauthorized", "not_found", "conflict", "rate_limited", "unavailable",
                    "not_implemented", "internal"]


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
    # The source preview (GET /api/preview/{doc_id}): the page or PDF scrolled to this quote, highlighted. Relative
    # to API_URL when it starts with /api/; mocks use a frontend path (/mocks/preview/…).
    preview_url: str
    preview_kind: Literal["page", "pdf", "text"]


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
    path: Literal["fast", "agent", "none", "cache"]  # cache: a quick question's checked answer, replayed
    latency_ms: float
    verified: bool  # quotes re-read from the index and claims checked against them


class ContactCard(Strict):
    """Who can help when the documents don't answer: every phone, e-mail and address is in its line_ids."""

    name: str  # institution / department
    area: str | None  # what it handles
    phone: list[str]
    email: list[str]
    address: str | None
    hours: str | None
    url: str
    site: str
    reason: str  # why this contact, one sentence in the answer's language
    line_ids: list[str]
    deep_link: str


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
    # The citation the UI opens right away in the source viewer (the question asks where exactly something
    # is written); null = only on click.
    focus_citation_id: str | None = None
    # who can help: copied from the cited lines when the person has to call or go somewhere, else for
    # not_found / partial the nearest contact cards; empty otherwise
    contacts: list[ContactCard] = []


# ─────────────── POST /api/feedback, GET /api/admin/feedback ───────────────

FeedbackTag = Literal["wrong", "outdated", "incomplete", "wrong_source", "not_understood", "helpful"]


class FeedbackRequest(Strict):
    answer_id: str = Field(min_length=1, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    vote: Literal["up", "down"] | None = None  # older clients: up = 5 stars, down = 1
    tags: list[FeedbackTag] = Field(default=[], max_length=6)
    comment: str | None = Field(default=None, max_length=2000)
    citation_id: str | None = Field(default=None, max_length=50)
    session_id: str | None = Field(default=None, max_length=100)  # the same session rating again overwrites

    @model_validator(mode="after")
    def rating_or_vote(self) -> "FeedbackRequest":  # quoted: Python 3.12 evaluates annotations eagerly
        if self.rating is None and self.vote is None:
            raise ValueError("rating (1-5) or vote is required")
        return self

    @property
    def stars(self) -> int:
        return self.rating if self.rating is not None else (5 if self.vote == "up" else 1)


class StaleSignal(Strict):
    """A cited passage the preview no longer finds on the live page."""

    doc_id: str = Field(min_length=1, max_length=500)


class FeedbackResponse(Strict):
    ok: bool


class FeedbackItem(Strict):
    answer_id: str
    rating: int
    tags: list[FeedbackTag]
    comment: str | None
    citation_id: str | None
    question: str | None
    lang: Lang | None
    status: AskStatus | None
    answer: str | None
    doc_ids: list[str]
    path: str | None
    created_at: str
    updated_at: str


class FeedbackList(Strict):
    items: list[FeedbackItem]  # lowest rating first, then newest


class TagCount(Strict):
    tag: FeedbackTag
    count: int


class FeedbackDay(Strict):
    day: str  # YYYY-MM-DD
    count: int
    average: float


class FeedbackStats(Strict):
    count: int
    average: float | None
    per_star: dict[str, int]  # "1".."5"
    top_tags: list[TagCount]
    by_day: list[FeedbackDay]  # oldest first


# ─────────────── GET /api/suggestions ───────────────


class Suggestion(Strict):
    id: int
    question: str
    lang: Lang
    answer_id: str | None
    asked_count: int
    rating_avg: float | None
    pinned: bool
    # admin list only: "ok" shown to people, "pending" until the next re-check answers it, "failed" not answered well
    check: Literal["ok", "pending", "failed"] | None = None
    # the same question in each language it is shown in ({"ro", "ru", "en"}); the page picks the UI language's
    texts: dict[str, str] | None = None


class SuggestionList(Strict):
    items: list[Suggestion]


class SuggestionCreate(Strict):
    question: str = Field(min_length=10, max_length=120)
    lang: Lang
    pinned: bool = True


# ─────────────── admin: gaps (questions without a full answer) ───────────────


class GapQuestion(Strict):
    answer_id: str
    question: str  # personal data masked (masking.mask)
    lang: Lang
    status: Literal["not_found", "partial"]
    ts: str


class GapSite(Strict):
    site: str
    hits: int  # how many of the group's answers found chunks of this site but didn't use them


class GapRecheck(Strict):
    status: AskStatus
    verified: bool
    answer_id: str
    ts: str


GapTopicKey = Literal["transport", "urbanism", "education", "health", "social", "utilities", "taxes", "documents",
                      "council", "environment", "culture", "other"]


class GapTitle(Strict):
    ro: str
    ru: str


class Gap(Strict):
    id: str  # the answer_id of the group's first question
    example: str
    title: GapTitle | None = None  # what is missing, named by the small model when it grouped the questions
    questions: list[GapQuestion]  # the latest 20
    count: int
    last_asked: str
    langs: list[Lang]
    status: Literal["not_found", "partial"]  # the worst in the group
    missing: list[str]  # what the partial answers said is missing
    hint_sites: list[GapSite]  # top 3: which department to ask
    rechecked: GapRecheck | None
    hidden: bool
    topic: GapTopicKey = "other"
    last_answer: str | None = None  # what the assistant said the last time it answered part of it


class GapTopic(Strict):
    topic: GapTopicKey
    groups: int


class GapTotals(Strict):
    not_found: int
    partial: int
    groups: int


class GapList(Strict):
    items: list[Gap]
    totals: GapTotals
    topics: list[GapTopic] = []  # groups per topic, biggest first


# ─────────────── admin: login ───────────────


class AdminLogin(Strict):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class AdminSession(Strict):
    token: str  # send as Authorization: Bearer <token>
    login: str
    expires_at: str  # ISO time; log in again after it


class AdminMe(Strict):
    login: str


# ─────────────── admin: sources and jobs ───────────────

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
# The admin starts crawl and refresh; the worker queues check (the nightly look for changes) and backlog (the
# autopilot) into the same table, and a retry repeats the kind, so any of them can be listed.
JobKind = Literal["crawl", "refresh", "check", "backlog"]


class Job(Strict):
    id: int
    source_id: int | None  # null = all sources
    kind: JobKind
    status: JobStatus
    stage: Literal["crawl", "download", "parse", "index"] | None
    stage_done: int
    stage_total: int
    percent: float  # 0..100 over all stages: crawl 20, download 20, parse 40, index 20
    eta_s: float | None
    started_at: str | None
    finished_at: str | None
    stats: dict[str, int]  # pages, documents_found, documents_downloaded, files_parsed, chunks, lines, …
    log_tail: list[str]  # last 50 lines of the running stage
    error: str | None


class JobList(Strict):
    jobs: list[Job]


class JobCreate(Strict):
    kind: Literal["crawl", "refresh"]


SourceStatus = Literal["indexed", "pending", "running", "queued", "failed", "blocked", "disabled"]


class SourceProgress(Strict):
    job_id: int
    stage: Literal["crawl", "download", "parse", "index"] | None
    percent: float
    eta_s: float | None
    current: str | None = None  # the file or address the stage is on right now, from the job's log


class SourceRow(Strict):
    """One row of the admin's single sources table: everything it shows, from one call."""

    id: int
    kind: Literal["site", "document"]
    url: str
    site_id: str  # domain
    title: str | None
    category: str | None
    category_source: str | None  # rule | keywords | default | toml | index | manual
    start_urls: list[str]
    max_depth: int | None
    max_pages: int | None
    enabled: bool
    robots: Literal["allowed", "blocked"]  # blocked: robots.txt forbids crawling, no crawl from the UI
    # disabled > blocked > running > queued > failed (last job) > indexed (has chunks) > pending
    status: SourceStatus
    pages: int
    documents_found: int
    documents_downloaded: int
    chunks: int  # in the index
    lines: int
    last_crawled: str | None
    progress: SourceProgress | None  # while a job is queued or running: poll every 2 s
    last_error: str | None  # the last job's error, short
    created_at: str
    last_job: Job | None
    # automatic updates (docs/history/audit/06-freshness-plan.md)
    auto_update: bool = True
    check_method: str | None = None  # wordpress | sitemap | sitemap-new+fingerprint | fingerprint …
    last_checked_at: str | None = None
    next_check_at: str | None = None
    stale_signals: int = 0  # people's signals since the last check
    # what the autopilot still has to do for this source (worker/schedule.py: next_backlog)
    crawl_left: int = 0         # pages the last crawl of it did not reach
    documents_pending: int = 0  # documents found but never downloaded
    files_pending: int = 0      # downloaded files not parsed yet
    pages_pending: int = 0      # crawled pages not parsed yet


class SourceList(Strict):
    sources: list[SourceRow]
    totals: "CorpusTotals"  # the page header needs no second call


class SourceCreate(Strict):
    """Only `url` is needed: the server decides the kind, category and crawl settings. The other fields are
    accepted for older clients and override what the server would decide."""

    url: str = Field(min_length=4, max_length=2000)
    kind: Literal["site", "document"] | None = None
    category: str | None = Field(default=None, max_length=50)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=20000)
    start: bool = True  # queue the crawl / download right away


class SourceDetected(Strict):
    kind: Literal["site", "document"]
    category: str
    category_source: str  # rule | keywords | default | manual (sent by the client) | existing (merged)
    title: str | None
    crawl_depth: int | None
    max_pages: int | None
    reason: str  # one short English sentence for the toast


class SourceAdded(SourceRow):
    detected: SourceDetected
    merged_into: int | None  # a deeper path or a document of a domain that is already a source: that source's id


class SourcePatch(Strict):
    enabled: bool | None = None
    auto_update: bool | None = None
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=20000)
    category: str | None = Field(default=None, max_length=50)


# ─────────────── errors: body of every non-2xx response ───────────────


class ApiError(Strict):
    error: ErrorCode
    message: str
    retry_after_s: float | None = None


# ─────────────── corpus stats (spott/api/stats.py): the totals of the admin's sources page ───────────────


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


# ─────────────── POST /api/visits ───────────────


class VisitRequest(Strict):
    visitor_id: str = Field(min_length=1, max_length=100)


class VisitorCount(Strict):
    visitors: int


# ─────────────── GET /health ───────────────


class HealthResponse(BaseModel):
    status: str
    device: str
    models_loaded: bool
    chunk_count: int
