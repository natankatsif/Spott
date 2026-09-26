"""Municipal assistant API. Contract: docs/API.md.

    uv run uvicorn app.main:app --reload --port 8000
"""

import asyncio
import functools
import json
import logging
import mimetypes
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote as url_quote

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool
from retrieval import (
    RERANKER_ENABLED,
    get_device,
    get_embedding_model,
    get_pool,
    get_reranker_model,
    retrieve,
)
from retrieval.db import init_app_db
from retrieval.sources import seed_sources
from retrieval.tools import (
    TOOL_SCHEMAS,
    grep_tool,
    open_tool,
    search_tool,
    toc_tool,
)
from starlette.concurrency import run_in_threadpool

from . import admin, errors, preview
from .admin import PgAdminStore
from .answering import answer_events, answer_question, replay_events, to_top_left
from .answers import PgAnswers
from .errors import ApiException, RateLimiter
from .files import DATA_DIR
from .gaps import PgGaps
from .llm import LLM, LLMUnavailable, OpenAILLM
from .pdf_source import PdfSource, make_clients
from .schemas import (
    AskRequest,
    AskResponse,
    CorpusStats,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    MatchedLine,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchTimings,
    SuggestionList,
    ToolGrepRequest,
    ToolOpenRequest,
    ToolSearchRequest,
    ToolTocRequest,
    WallResponse,
)
from .stats import corpus_stats
from .store import PgStore
from .suggestions import PgSuggestions
from .wall import Wall

log = logging.getLogger("backend")

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
                if o.strip()]
ASK_RATE_LIMIT = int(os.getenv("ASK_RATE_LIMIT", "10"))  # questions per minute per client
# Quick questions are re-asked (LLM calls) when the index changes and daily; checked every this many seconds.
SUGGESTIONS_EVERY_S = float(os.getenv("SUGGESTIONS_EVERY_S", "600"))
SUGGESTIONS_RECHECK = os.getenv("SUGGESTIONS_RECHECK", "true").lower() in ("1", "true", "yes")
FEEDBACK_DIR = Path(__file__).resolve().parents[2] / "data" / "feedback"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("Starting up Municipal Assistant API...")
    device = get_device()
    app.state.device = device
    app.state.models_loaded = False
    app.state.chunk_count = 0
    app.state.wall = Wall()
    app.state.rate_limiter = RateLimiter(limit=ASK_RATE_LIMIT)

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
    app.state.answers = PgAnswers(pool)
    app.state.suggestions = PgSuggestions(pool)
    app.state.gaps = PgGaps(pool)
    # The admin's gap re-check: one question, one model call (no rewrite call, no second pass).
    app.state.ask_once = lambda req: answer_question(app.state.store, get_llm(), req, pool=pool, freshness=False,
                                                     rewrite=False, on_done=answered)
    http_clients = make_clients()
    app.state.http = http_clients[0]
    app.state.pdf_source = PdfSource(*http_clients)
    app.state.pages = preview.PageSource(http_clients[0])

    log.info("Loading embedding model on %s...", device)
    await run_in_threadpool(get_embedding_model, device)
    if RERANKER_ENABLED:
        log.info("Loading reranker model on %s...", device)
        await run_in_threadpool(get_reranker_model, device)
    else:
        log.info("Reranker is disabled (RERANKER_ENABLED=false), skipping reranker pre-load.")
    app.state.models_loaded = True

    # Warm-up query compiles MPS/CUDA kernels before the first real question.
    try:
        await run_in_threadpool(retrieve, pool, "warmup query", k=2, rerank=False)
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
app.include_router(admin.auth_router)
app.include_router(admin.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # CORS_ORIGINS=* for the widget embedded on other sites
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_pool() -> ConnectionPool:
    pool = getattr(app.state, "pool", None)
    if pool is None:
        raise ApiException(503, "unavailable", "Database pool not initialized")
    return pool


def get_llm() -> LLM:
    # Created on the first question, so the server starts (and /api/search works) without an API key.
    if getattr(app.state, "llm", None) is None:
        try:
            app.state.llm = OpenAILLM()
        except LLMUnavailable as e:
            raise ApiException(503, "unavailable", f"LLM not configured: {e}") from e
    return app.state.llm


def client_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def prepare_ask(request: Request) -> tuple[ConnectionPool, LLM]:
    """Checks done before answering, so /api/ask/stream fails with an HTTP status, not mid-stream."""
    pool = require_pool()
    if not getattr(app.state, "models_loaded", False):
        raise ApiException(503, "unavailable", "Service is warming up, try again in a few seconds", 5)
    llm = get_llm()
    app.state.rate_limiter.check(client_id(request))
    return pool, llm


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        device=getattr(app.state, "device", "unknown"),
        models_loaded=getattr(app.state, "models_loaded", False),
        chunk_count=getattr(app.state, "chunk_count", 0),
    )


# ─────────────── answers ───────────────


def cached_answer(req: AskRequest) -> AskResponse | None:
    suggestions = getattr(app.state, "suggestions", None)
    try:
        return suggestions.cached(req) if suggestions is not None else None
    except Exception as e:
        log.warning("quick question cache not read: %s", e)
        return None


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    pool, llm = prepare_ask(request)
    if cached := await run_in_threadpool(cached_answer, req):
        for event in replay_events(cached, req, on_done=answered):
            if event["type"] == "done":
                return AskResponse.model_validate(event["response"])
    try:
        return await run_in_threadpool(answer_question, app.state.store, llm, req, pool=pool,
                                       on_done=answered)
    except LLMUnavailable as e:
        log.warning("LLM call failed: %s", e)
        raise ApiException(503, "unavailable", "LLM unavailable, try again") from e


def answered(req: AskRequest, resp: AskResponse, info: dict | None = None) -> None:
    """Every answer: onto the question wall, and into `answers` for ratings, quick questions and the admin's gaps."""
    app.state.wall.add(req, resp)
    answers = getattr(app.state, "answers", None)
    if answers is not None:
        answers.record(req, resp, info or {})


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
    pool, llm = prepare_ask(request)

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
    cited documents and path of the answer; without a database (local UI work) appended to data/feedback."""
    answers = getattr(app.state, "answers", None)
    if answers is not None:
        if not answers.rate(req):
            raise ApiException(404, "not_found", f"Unknown answer {req.answer_id}")
        return FeedbackResponse(ok=True)
    record = req.model_dump() | {"rating": req.stars, "ts": datetime.now(UTC).isoformat(timespec="seconds")}
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with (FEEDBACK_DIR / f"{datetime.now(UTC):%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
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
                         lang: str = Query("ro", pattern="^(ro|ru)$"), embed: int = Query(1, ge=0, le=1)) -> Response:
    """The cited source for the chat's iframe: the page (our sanitized copy) or the PDF (pdf.js), scrolled to the
    quoted lines and highlighted; DOCX and unreachable pages as our text view. See app/preview.py."""
    store = getattr(app.state, "store", None)
    doc = await run_in_threadpool(store.preview_document, doc_id) if store else None
    if doc is None:
        raise ApiException(404, "not_found", f"Unknown document {doc_id}")
    raw = await run_in_threadpool(store.doc_lines, doc_id)
    lines = [preview_line(r, doc.get("page_sizes") or []) for r in raw]
    known = {ln["line_id"] for ln in lines}
    selected = [lid for lid in dict.fromkeys(line or []) if lid in known][:preview.MAX_LINES]
    kind = preview.preview_kind(doc["kind"], doc["url"], doc["has_file"])
    deep = preview.deep_link_for(doc, lines, selected, kind)
    common = {"doc": doc, "lines": lines, "selected": selected, "lang": lang, "embed": bool(embed),
              "allowed": CORS_ORIGINS}
    if kind == "pdf":
        view = preview.pdf_view(**common, file_url=f"/api/documents/{url_quote(doc_id, safe='')}/file", deep_link=deep)
    elif kind == "page" and (got := await app.state.pages.get(doc["url"])):
        view = preview.page_view(**common, page_html=got[0], how=got[1], date=got[2], deep_link=deep)
    else:
        view = preview.text_view(**common, deep_link=deep, unavailable=kind == "page")
    return Response(view.body, media_type="text/html; charset=utf-8", headers=preview.headers(view, CORS_ORIGINS))


def preview_line(row: dict, page_sizes: list[dict]) -> dict:
    boxes = row.get("bboxes") or [b for b in row.get("chunk_bboxes") or [] if b.get("page") == row.get("page")]
    return {"line_id": row["line_id"], "text": row["text"], "page": row.get("page") or (row.get("pages") or [None])[0],
            "bboxes": [b.model_dump() for b in to_top_left(boxes, page_sizes)]}


@app.get("/api/wall", response_model=WallResponse)
def wall(after: str | None = None, limit: int = Query(default=50, ge=1, le=200)) -> WallResponse:
    return app.state.wall.since(after, limit)


@app.get("/api/suggestions", response_model=SuggestionList)
async def suggestions(lang: str = Query("ro", pattern="^(ro|ru)$"), limit: int = Query(6, ge=1, le=20)) -> SuggestionList:
    """Real questions we know we answer well (answered, verified, re-checked against the current index)."""
    store = getattr(app.state, "suggestions", None)
    if store is None:
        return SuggestionList(items=[])
    return SuggestionList(items=await run_in_threadpool(store.list, lang, limit))


@app.get("/api/corpus/stats", response_model=CorpusStats)
async def stats() -> CorpusStats:
    return await run_in_threadpool(corpus_stats, require_pool())


# ─────────────── search and agent tools ───────────────


@app.post("/api/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest) -> SearchResponse:
    pool = require_pool()
    try:
        res = await run_in_threadpool(retrieve, pool, req.query, lang=req.lang, k=req.k, rerank=req.rerank)
    except ValueError as e:
        raise ApiException(422, "validation_error", str(e)) from e

    results = []
    for c in res.items:
        pages = c.get("pages")
        results.append(SearchResultItem(
            chunk_id=c["chunk_id"],
            doc_id=c.get("doc_id", ""),
            citation_label=c.get("citation_label", ""),
            text=c.get("text", ""),
            url=c.get("url", ""),
            found_on=c.get("found_on"),
            site=c.get("site"),
            lang=c.get("lang"),
            page=pages[0] if isinstance(pages, list) and pages else None,
            parent_legal_path=c.get("parent_legal_path"),
            rerank_score=c.get("rerank_score"),
            vec_rank=c.get("vec_rank"),
            fts_rank=c.get("fts_rank"),
            matched_lines=[MatchedLine(line_id=m["line_id"], idx=m["idx"], text=m["text"], score=m.get("score"))
                           for m in c.get("matched_lines", [])],
        ))
    t = res.timings_ms
    return SearchResponse(
        results=results,
        timings_ms=SearchTimings(embed=t.get("embed", 0.0), vector_sql=t.get("vector_sql", 0.0),
                                 fts_sql=t.get("fts_sql", 0.0), rerank=t.get("rerank", 0.0),
                                 total=t.get("total", 0.0)),
        not_found=res.not_found,
    )


@app.get("/api/tools/schemas")
def tool_schemas() -> list[dict]:
    return TOOL_SCHEMAS


@app.post("/api/tools/search")
async def tool_search(req: ToolSearchRequest) -> dict:
    return await run_in_threadpool(search_tool, require_pool(), req.query, lang=req.lang, site=req.site, k=req.k)


@app.post("/api/tools/grep")
async def tool_grep(req: ToolGrepRequest) -> dict:
    return await run_in_threadpool(grep_tool, require_pool(), req.pattern, doc_id=req.doc_id, site=req.site,
                                   limit=req.limit)


@app.post("/api/tools/toc")
async def tool_toc(req: ToolTocRequest) -> dict:
    return await run_in_threadpool(toc_tool, require_pool(), req.doc_id)


@app.post("/api/tools/open")
async def tool_open(req: ToolOpenRequest) -> dict:
    return await run_in_threadpool(open_tool, require_pool(), req.doc_id, node_id=req.node_id,
                                   chunk_id=req.chunk_id, max_lines=req.max_lines)
