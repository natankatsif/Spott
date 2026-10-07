"""The public API (docs/API.md): health, questions (whole or streamed), ratings and outdated-content signals, the
cited documents' PDFs and previews, quick questions, the visitor counter. The services come from app.state
(main.lifespan): a route takes them through deps.service, or does without the optional ones."""

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from . import freshness, preview
from .answering import answer_events, answer_question, replay_events
from .deps import llm, service
from .errors import ApiException, client_address
from .llm import LLM, LLMUnavailable
from .schemas import (
    AskRequest,
    AskResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    StaleSignal,
    SuggestionList,
    VisitorCount,
    VisitRequest,
)

log = logging.getLogger("backend")

# The sites that may embed us: the widget's API calls (CORS) and the preview iframe (CSP frame-ancestors).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
                if o.strip()]
NO_DATABASE = "Database pool not initialized"

router = APIRouter()


class IndexSize:
    """Chunks with embeddings, read from the database at most every ttl_s: /health follows a reindex in progress (the
    index grows while the API keeps serving) instead of the count at startup."""

    def __init__(self, ttl_s: float = 10.0):
        self.ttl_s, self.count, self.read_at = ttl_s, 0, float("-inf")

    def get(self, pool: ConnectionPool | None) -> int:
        if pool is not None and time.monotonic() - self.read_at >= self.ttl_s:
            try:
                with pool.connection() as conn, conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
                    self.count = cur.fetchone()[0]
                self.read_at = time.monotonic()
            except Exception as e:
                log.warning("chunk count not read: %s", e)
        return self.count


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    state = request.app.state
    size: IndexSize | None = getattr(state, "index_size", None)
    return HealthResponse(
        status="ok",
        device=getattr(state, "device", "unknown"),
        models_loaded=getattr(state, "models_loaded", False),
        chunk_count=size.get(getattr(state, "pool", None)) if size else 0,
    )


# ─────────────── answers ───────────────


def recorder(state) -> Callable[[AskRequest, AskResponse, dict | None], None]:
    """on_done of every answer: into `answers`, for ratings, quick questions and the admin's gaps."""
    def answered(req: AskRequest, resp: AskResponse, info: dict | None = None) -> None:
        if (answers := getattr(state, "answers", None)) is not None:
            answers.record(req, resp, info or {})

    return answered


def cached_answer(state, req: AskRequest) -> AskResponse | None:
    """A quick question's checked answer, if this question is one."""
    suggestions = getattr(state, "suggestions", None)
    try:
        return suggestions.cached(req) if suggestions is not None else None
    except Exception as e:
        log.warning("quick question cache not read: %s", e)
        return None


def replayed_answer(state, req: AskRequest) -> AskResponse | None:
    """A quick question's checked answer replayed as a new answer, None when it is not cached. Both the cache and
    the record of the answer are Postgres queries: the caller runs this in a worker thread."""
    if cached := cached_answer(state, req):
        for event in replay_events(cached, req, on_done=recorder(state)):
            if event["type"] == "done":
                return AskResponse.model_validate(event["response"])
    return None


async def prepare_ask(request: Request) -> tuple[ConnectionPool, LLM]:
    """Checks done before answering, so /api/ask/stream fails with an HTTP status, not mid-stream: the database, the
    embedding model loaded, the answer model configured, the client's rate limit, in this order. The model settings
    are read in a worker thread: every 15 s they are re-read from Postgres under a lock, and on the event loop that
    would hold up every request, /health included."""
    state = request.app.state
    pool = service(request, "pool", NO_DATABASE)
    if not getattr(state, "models_loaded", False):
        raise ApiException(503, "unavailable", "Service is warming up, try again in a few seconds", 5)
    model = await run_in_threadpool(llm, state)
    state.rate_limiter.check(client_address(request))
    return pool, model


@router.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    pool, model = await prepare_ask(request)
    state = request.app.state
    if replayed := await run_in_threadpool(replayed_answer, state, req):
        return replayed
    try:
        return await run_in_threadpool(answer_question, state.store, model, req, pool=pool, on_done=recorder(state))
    except LLMUnavailable as e:
        log.warning("LLM call failed: %s", e)
        raise ApiException(503, "unavailable", "LLM unavailable, try again") from e


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/api/ask/stream")
async def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
    pool, model = await prepare_ask(request)
    state = request.app.state

    def events() -> Iterator[str]:  # sync: Starlette iterates it in a worker thread
        try:
            cached = cached_answer(state, req)
            stream = replay_events(cached, req, on_done=recorder(state)) if cached else answer_events(
                state.store, model, req, pool=pool, on_done=recorder(state))
            for event in stream:
                yield sse(event)
        except LLMUnavailable as e:
            log.warning("LLM call failed: %s", e)
            yield sse({"type": "error", "code": "unavailable", "message": "LLM unavailable, try again"})
        except Exception:
            log.exception("answer stream failed")
            yield sse({"type": "error", "code": "internal", "message": "Internal error"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest, request: Request) -> FeedbackResponse:
    """1-5 stars (or the older up/down vote), reason tags, comment. Stored in Postgres with the question, status,
    cited documents and path of the answer."""
    answers = service(request, "answers", NO_DATABASE)
    if not answers.rate(req):
        raise ApiException(404, "not_found", f"Unknown answer {req.answer_id}")
    if "outdated" in req.tags and (pool := getattr(request.app.state, "pool", None)) is not None:
        try:  # "outdated": check the cited sites sooner (never breaks the rating)
            freshness.signal_answer(pool, req.answer_id)
        except Exception as e:
            log.warning("outdated signal not recorded: %s", e)
    return FeedbackResponse(ok=True)


@router.post("/api/signals/outdated", response_model=FeedbackResponse)
def outdated(req: StaleSignal, request: Request) -> FeedbackResponse:
    """The preview didn't find a cited passage on the live page: the site is checked sooner (at most every 6 h)."""
    request.app.state.signal_limiter.check(client_address(request))
    freshness.signal_documents(service(request, "pool", NO_DATABASE), [req.doc_id])
    return FeedbackResponse(ok=True)


# ─────────────── the cited documents ───────────────


@router.get("/api/documents/{doc_id:path}/file")
async def document_file(doc_id: str, request: Request) -> Response:
    """The PDF for the source viewer, fetched from the city hall site by the URL in our index."""
    store = getattr(request.app.state, "store", None)
    doc = await run_in_threadpool(store.document, doc_id) if store else None
    if doc is None:
        raise ApiException(404, "not_found", f"Unknown document {doc_id}")
    data = await request.app.state.pdf_source.get(doc)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=3600"})


@router.get("/api/preview/{doc_id:path}")
async def source_preview(doc_id: str, request: Request, line: Annotated[list[str] | None, Query()] = None,
                         lang: str = Query("ro", pattern="^(ro|ru|en)$"), embed: int = Query(1, ge=0, le=1)) -> Response:
    """The cited source for the chat's iframe: the page (our sanitized copy) or the PDF (pdf.js), scrolled to the
    quoted lines and highlighted; DOCX and unreachable pages as our text view. See spott/api/preview.py."""
    store = getattr(request.app.state, "store", None)
    doc = await run_in_threadpool(store.preview_document, doc_id) if store else None
    if doc is None:
        raise ApiException(404, "not_found", f"Unknown document {doc_id}")
    rows = await run_in_threadpool(store.doc_lines, doc_id)
    view = await preview.source_view(doc, rows, line, lang, bool(embed), CORS_ORIGINS, request.app.state.pages)
    return Response(view.body, media_type="text/html; charset=utf-8", headers=preview.headers(view, CORS_ORIGINS))


# ─────────────── the home screen ───────────────


@router.get("/api/suggestions", response_model=SuggestionList)
async def suggestions(request: Request, lang: str = Query("ro", pattern="^(ro|ru|en)$"),
                      limit: int = Query(6, ge=1, le=20)) -> SuggestionList:
    """Real questions we know we answer well (answered, verified, re-checked against the current index)."""
    store = getattr(request.app.state, "suggestions", None)
    if store is None:
        return SuggestionList(items=[])
    return SuggestionList(items=await run_in_threadpool(store.list, lang, limit))


@router.post("/api/visits", response_model=VisitorCount)
async def visit(req: VisitRequest, request: Request) -> VisitorCount:
    """Counts this browser once (its anonymous id) and returns the number of unique visitors, for the header."""
    visitors = service(request, "visitors", NO_DATABASE)
    return VisitorCount(visitors=await run_in_threadpool(visitors.visit, req.visitor_id))
