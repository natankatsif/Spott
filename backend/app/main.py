"""Municipal assistant API. Contract: docs/API.md.

    uv run uvicorn app.main:app --reload --port 8000
"""

import json
import logging
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from psycopg_pool import ConnectionPool
from retrieval import (
    RERANKER_ENABLED,
    get_device,
    get_embedding_model,
    get_pool,
    get_reranker_model,
    retrieve,
)
from retrieval.tools import (
    TOOL_SCHEMAS,
    grep_tool,
    open_tool,
    search_tool,
    toc_tool,
)
from starlette.concurrency import run_in_threadpool

from . import errors
from .answering import answer_events, answer_question
from .errors import ApiException, RateLimiter
from .files import raw_pdf
from .llm import LLM, LLMUnavailable, OpenAILLM
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
    ToolGrepRequest,
    ToolOpenRequest,
    ToolSearchRequest,
    ToolTocRequest,
    WallResponse,
)
from .stats import corpus_stats
from .store import PgStore
from .wall import Wall

log = logging.getLogger("backend")

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
                if o.strip()]
ASK_RATE_LIMIT = int(os.getenv("ASK_RATE_LIMIT", "10"))  # questions per minute per client
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

    yield

    log.info("Shutting down Municipal Assistant API...")
    pool.close()


app = FastAPI(title="Chișinău Municipal Assistant", lifespan=lifespan)
errors.install(app)
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


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    pool, llm = prepare_ask(request)
    try:
        return await run_in_threadpool(answer_question, app.state.store, llm, req, pool=pool,
                                       on_done=app.state.wall.add)
    except LLMUnavailable as e:
        log.warning("LLM call failed: %s", e)
        raise ApiException(503, "unavailable", "LLM unavailable, try again") from e


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
    pool, llm = prepare_ask(request)

    def events() -> Iterator[str]:  # sync: Starlette iterates it in a worker thread
        try:
            for event in answer_events(app.state.store, llm, req, pool=pool, on_done=app.state.wall.add):
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
    record = req.model_dump() | {"ts": datetime.now(UTC).isoformat(timespec="seconds")}
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with (FEEDBACK_DIR / f"{datetime.now(UTC):%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return FeedbackResponse(ok=True)


@app.get("/api/documents/{doc_id:path}/file")
async def document_file(doc_id: str) -> FileResponse:
    doc = await run_in_threadpool(app.state.store.document, doc_id) if getattr(app.state, "store", None) else None
    path = raw_pdf(doc_id, doc.get("sha256") if doc else None) if doc else None
    if path is None:
        raise ApiException(404, "not_found", f"No stored PDF for {doc_id}")
    return FileResponse(path, media_type="application/pdf", content_disposition_type="inline")


@app.get("/api/wall", response_model=WallResponse)
def wall(after: str | None = None, limit: int = Query(default=50, ge=1, le=200)) -> WallResponse:
    return app.state.wall.since(after, limit)


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
