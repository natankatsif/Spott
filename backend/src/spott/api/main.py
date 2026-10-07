"""Municipal assistant API. Contract: docs/API.md.

    uv run uvicorn spott.api.main:app --reload --port 8000
"""

import asyncio
import functools
import json
import logging
import mimetypes
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from spott.core.db import get_pool, init_app_db
from spott.core.embeddings import get_device, get_embedding_model
from spott.core.paths import DATA_DIR
from spott.core.pipeline import retrieve
from spott.core.sources import seed_sources

from . import admin, errors, freshness, preview, usage
from .admin.gaps import PgGaps, llm_cluster
from .admin.store import PgAdminStore
from .answering import answer_events, answer_question, replay_events
from .answers import PgAnswers
from .errors import ApiException, RateLimiter, client_address
from .llm import LLM, LLMUnavailable
from .llm_settings import LLMHolder, PgSettings
from .pdf_source import PdfSource, make_clients
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
from .store import PgStore
from .suggestions import PgSuggestions, llm_translate
from .visitors import PgVisitors

log = logging.getLogger("backend")

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
                if o.strip()]
ASK_RATE_LIMIT = int(os.getenv("ASK_RATE_LIMIT", "10"))  # questions per minute per client
# Quick questions are re-asked (LLM calls) when the index changes and daily; checked every this many seconds.
SUGGESTIONS_EVERY_S = float(os.getenv("SUGGESTIONS_EVERY_S", "600"))
SUGGESTIONS_RECHECK = os.getenv("SUGGESTIONS_RECHECK", "true").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("Starting up Municipal Assistant API...")
    device = get_device()
    app.state.device = device
    app.state.models_loaded = False
    app.state.chunk_count = 0
    app.state.rate_limiter = RateLimiter(limit=ASK_RATE_LIMIT)
    app.state.signal_limiter = RateLimiter(limit=20)  # outdated-content signals per client per minute

    pool = get_pool(min_size=2, max_size=10)
    app.state.pool = pool
    app.state.store = PgStore(pool)
    try:  # app-state tables (sources, jobs, answers, feedback, suggestions, contacts), sources seeded
        with pool.connection() as conn:
            init_app_db(conn)
            added = seed_sources(conn, DATA_DIR / "sources" / "sites.toml")
        if added:
            log.info("Sources seeded: %d added", added)
    except Exception as e:
        log.warning("App tables not created: %s", e)
    app.state.admin = PgAdminStore(pool)
    app.state.usage = usage.PgUsage(pool)
    app.state.llm_holder = LLMHolder(PgSettings(pool), on_usage=record_usage)
    app.state.answers = PgAnswers(pool)
    app.state.suggestions = PgSuggestions(pool)
    app.state.translate = llm_translate(get_llm)  # a pinned question into the other language
    app.state.gaps = PgGaps(pool, cluster=llm_cluster(get_llm))
    app.state.visitors = PgVisitors(pool)
    # The admin's gap re-check: one question, one model call (no routing, rewrite or translation call, no second pass).
    app.state.ask_once = lambda req: answer_question(app.state.store, get_llm(), req, pool=pool, freshness=False,
                                                     rewrite=False, routing=False,
                                                     translate=False, on_done=answered)
    http_clients = make_clients()
    app.state.http = http_clients[0]
    app.state.pdf_source = PdfSource(*http_clients)
    app.state.pages = preview.PageSource(http_clients[0], app.state.pool)

    log.info("Loading embedding model on %s...", device)
    await run_in_threadpool(get_embedding_model, device)
    app.state.models_loaded = True

    # Warm-up query compiles MPS/CUDA kernels before the first real question.
    try:
        await run_in_threadpool(retrieve, pool, "warmup query", k=2)
    except Exception as e:
        log.warning("Warmup search query encountered error (ignored): %s", e)

    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
            app.state.chunk_count = cur.fetchone()[0]
        log.info("Indexed chunks ready: %d", app.state.chunk_count)
    except Exception as e:
        log.warning("Could not query chunk count on startup: %s", e)

    recheck = asyncio.create_task(recheck_suggestions()) if SUGGESTIONS_RECHECK else None
    yield

    log.info("Shutting down Municipal Assistant API...")
    if recheck:
        recheck.cancel()
    for client in http_clients:
        await client.aclose()
    pool.close()


async def recheck_suggestions() -> None:
    """Quick questions: candidates from the answers log (seeds while it is empty), each re-asked when the index
    changed or a day has passed; one that is no longer answered and verified is dropped."""
    while True:
        try:
            suggestions = app.state.suggestions
            await run_in_threadpool(suggestions.refresh)
            result = await run_in_threadpool(suggestions.recheck, functools.partial(
                answer_question, app.state.store, get_llm(), pool=app.state.pool))
            if result["checked"]:
                log.info("quick questions re-checked: %s", result)
        except Exception as e:
            log.warning("quick questions not re-checked: %s", e)
        await asyncio.sleep(SUGGESTIONS_EVERY_S)


app = FastAPI(title="Chișinău Municipal Assistant", lifespan=lifespan)
errors.install(app)
mimetypes.add_type("text/javascript", ".mjs")  # pdf.js is ES modules: a module script needs a JS type
app.mount(preview.STATIC_PREFIX, StaticFiles(directory=preview.STATIC), name="preview-static")
for router in admin.ROUTERS:
    app.include_router(router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # CORS_ORIGINS=* for the widget embedded on other sites
    allow_methods=["*"],
    allow_headers=["*"],
)


def record_usage(provider: str, model: str, role: str, kind: str, result, ms: int) -> None:
    """Every routed model call's tokens, for admin → Spending."""
    log_ = getattr(app.state, "usage", None)
    if log_ is not None:
        log_.record(usage.Call(provider=provider, model=model, role=role, kind=kind,
                               input_tokens=result.prompt_tokens or 0, output_tokens=result.completion_tokens or 0,
                               ms=ms))


def require_pool() -> ConnectionPool:
    pool = getattr(app.state, "pool", None)
    if pool is None:
        raise ApiException(503, "unavailable", "Database pool not initialized")
    return pool


def get_llm() -> LLM:
    # Created on the first question, so the server starts without an API key; the keys and
    # models come from the environment and the admin's settings (admin → Models), re-read after a change.
    if (fixed := getattr(app.state, "llm", None)) is not None:
        return fixed
    holder = getattr(app.state, "llm_holder", None)
    if holder is None:
        holder = app.state.llm_holder = LLMHolder(None)
    try:
        return holder.get()
    except LLMUnavailable as e:
        raise ApiException(503, "unavailable", f"LLM not configured: {e}") from e


def client_id(request: Request) -> str:
    return client_address(request)


async def prepare_ask(request: Request) -> tuple[ConnectionPool, LLM]:
    """Checks done before answering, so /api/ask/stream fails with an HTTP status, not mid-stream. get_llm() runs in
    a worker thread: every 15 s it re-reads the admin's model settings from Postgres under a lock, and on the event
    loop that would hold up every request, /health included."""
    pool = require_pool()
    if not getattr(app.state, "models_loaded", False):
        raise ApiException(503, "unavailable", "Service is warming up, try again in a few seconds", 5)
    llm = await run_in_threadpool(get_llm)
    app.state.rate_limiter.check(client_id(request))
    return pool, llm


HEALTH_COUNT_TTL_S = 10.0
_chunk_count_at = 0.0


def current_chunk_count() -> int:
    """Chunks with embeddings, read from the database at most every 10 s: /health follows a reindex in progress
    (the index grows while the API keeps serving) instead of the count at startup."""
    global _chunk_count_at
    pool = getattr(app.state, "pool", None)
    if pool is not None and time.monotonic() - _chunk_count_at >= HEALTH_COUNT_TTL_S:
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
                app.state.chunk_count = cur.fetchone()[0]
            _chunk_count_at = time.monotonic()
        except Exception as e:
            log.warning("chunk count not read: %s", e)
    return getattr(app.state, "chunk_count", 0)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        device=getattr(app.state, "device", "unknown"),
        models_loaded=getattr(app.state, "models_loaded", False),
        chunk_count=current_chunk_count(),
    )


# ─────────────── answers ───────────────


def cached_answer(req: AskRequest) -> AskResponse | None:
    suggestions = getattr(app.state, "suggestions", None)
    try:
        return suggestions.cached(req) if suggestions is not None else None
    except Exception as e:
        log.warning("quick question cache not read: %s", e)
        return None


def replayed_answer(req: AskRequest) -> AskResponse | None:
    """A quick question's checked answer replayed as a new answer, None when it is not cached. Both the cache and
    the record of the answer (answered) are Postgres queries: the caller runs this in a worker thread."""
    if cached := cached_answer(req):
        for event in replay_events(cached, req, on_done=answered):
            if event["type"] == "done":
                return AskResponse.model_validate(event["response"])
    return None


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    pool, llm = await prepare_ask(request)
    if replayed := await run_in_threadpool(replayed_answer, req):
        return replayed
    try:
        return await run_in_threadpool(answer_question, app.state.store, llm, req, pool=pool,
                                       on_done=answered)
    except LLMUnavailable as e:
        log.warning("LLM call failed: %s", e)
        raise ApiException(503, "unavailable", "LLM unavailable, try again") from e


def answered(req: AskRequest, resp: AskResponse, info: dict | None = None) -> None:
    """Every answer into `answers`, for ratings, quick questions and the admin's gaps."""
    answers = getattr(app.state, "answers", None)
    if answers is not None:
        answers.record(req, resp, info or {})


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
    pool, llm = await prepare_ask(request)

    def events() -> Iterator[str]:  # sync: Starlette iterates it in a worker thread
        try:
            cached = cached_answer(req)
            stream = replay_events(cached, req, on_done=answered) if cached else answer_events(
                app.state.store, llm, req, pool=pool, on_done=answered)
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


@app.post("/api/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """1-5 stars (or the older up/down vote), reason tags, comment. Stored in Postgres with the question, status,
    cited documents and path of the answer."""
    answers = getattr(app.state, "answers", None)
    if answers is None:
        raise ApiException(503, "unavailable", "Database pool not initialized")
    if not answers.rate(req):
        raise ApiException(404, "not_found", f"Unknown answer {req.answer_id}")
    if "outdated" in req.tags and (pool := getattr(app.state, "pool", None)) is not None:
        try:  # "outdated": check the cited sites sooner (never breaks the rating)
            freshness.signal_answer(pool, req.answer_id)
        except Exception as e:
            log.warning("outdated signal not recorded: %s", e)
    return FeedbackResponse(ok=True)


@app.post("/api/signals/outdated", response_model=FeedbackResponse)
def outdated(req: StaleSignal, request: Request) -> FeedbackResponse:
    """The preview didn't find a cited passage on the live page: the site is checked sooner (at most every 6 h)."""
    app.state.signal_limiter.check(client_address(request))
    freshness.signal_documents(require_pool(), [req.doc_id])
    return FeedbackResponse(ok=True)


@app.get("/api/documents/{doc_id:path}/file")
async def document_file(doc_id: str) -> Response:
    """The PDF for the source viewer, fetched from the city hall site by the URL in our index."""
    store = getattr(app.state, "store", None)
    doc = await run_in_threadpool(store.document, doc_id) if store else None
    if doc is None:
        raise ApiException(404, "not_found", f"Unknown document {doc_id}")
    data = await app.state.pdf_source.get(doc)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=3600"})


@app.get("/api/preview/{doc_id:path}")
async def source_preview(doc_id: str, line: Annotated[list[str] | None, Query()] = None,
                         lang: str = Query("ro", pattern="^(ro|ru|en)$"), embed: int = Query(1, ge=0, le=1)) -> Response:
    """The cited source for the chat's iframe: the page (our sanitized copy) or the PDF (pdf.js), scrolled to the
    quoted lines and highlighted; DOCX and unreachable pages as our text view. See spott/api/preview.py."""
    store = getattr(app.state, "store", None)
    doc = await run_in_threadpool(store.preview_document, doc_id) if store else None
    if doc is None:
        raise ApiException(404, "not_found", f"Unknown document {doc_id}")
    raw = await run_in_threadpool(store.doc_lines, doc_id)
    lines = [preview.preview_line(r, doc.get("page_sizes") or []) for r in raw]
    known = {ln["line_id"] for ln in lines}
    selected = [lid for lid in dict.fromkeys(line or []) if lid in known][:preview.MAX_LINES]
    kind = preview.preview_kind(doc["kind"], doc["url"], doc["has_file"])
    deep = preview.deep_link_for(doc, lines, selected, kind)
    common = {"doc": doc, "lines": lines, "selected": selected, "lang": lang, "embed": bool(embed),
              "allowed": CORS_ORIGINS}
    if kind == "pdf":
        view = preview.pdf_view(**common, file_url=preview.file_url(doc_id), deep_link=deep)
    elif kind == "page" and (got := await app.state.pages.get(doc["url"])):
        view = preview.page_view(**common, page_html=got[0], how=got[1], date=got[2], deep_link=deep)
    else:
        view = preview.text_view(**common, deep_link=deep, unavailable=kind == "page")
    return Response(view.body, media_type="text/html; charset=utf-8", headers=preview.headers(view, CORS_ORIGINS))


@app.get("/api/suggestions", response_model=SuggestionList)
async def suggestions(lang: str = Query("ro", pattern="^(ro|ru|en)$"), limit: int = Query(6, ge=1, le=20)) -> SuggestionList:
    """Real questions we know we answer well (answered, verified, re-checked against the current index)."""
    store = getattr(app.state, "suggestions", None)
    if store is None:
        return SuggestionList(items=[])
    return SuggestionList(items=await run_in_threadpool(store.list, lang, limit))


@app.post("/api/visits", response_model=VisitorCount)
async def visit(req: VisitRequest) -> VisitorCount:
    """Counts this browser once (its anonymous id) and returns the number of unique visitors, for the header."""
    visitors = getattr(app.state, "visitors", None)
    if visitors is None:
        raise ApiException(503, "unavailable", "Database pool not initialized")
    return VisitorCount(visitors=await run_in_threadpool(visitors.visit, req.visitor_id))
