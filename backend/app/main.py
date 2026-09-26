"""Municipal assistant API.

    uv run uvicorn app.main:app --reload --port 8000
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

from .schemas import (
    AskRequest,
    AskResponse,
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
)

log = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("Starting up Municipal Assistant API...")
    device = get_device()
    app.state.device = device
    app.state.models_loaded = False
    app.state.chunk_count = 0

    # 1. Connection pool
    pool = get_pool(min_size=2, max_size=10)
    app.state.pool = pool

    # 2. Pre-load models in worker thread
    log.info("Loading embedding model on %s...", device)
    await run_in_threadpool(get_embedding_model, device)
    if RERANKER_ENABLED:
        log.info("Loading reranker model on %s...", device)
        await run_in_threadpool(get_reranker_model, device)
    else:
        log.info("Reranker is disabled (RERANKER_ENABLED=false), skipping reranker pre-load.")
    app.state.models_loaded = True

    # 3. Warm-up search query to compile MPS/CUDA kernels
    log.info("Running warm-up search query...")
    try:
        await run_in_threadpool(retrieve, pool, "warmup query", k=2, rerank=False)
    except Exception as e:
        log.warning("Warmup search query encountered error (ignored): %s", e)

    # 4. Check indexed chunk count
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NOT_FOUND = {
    "ro": "Informația nu a fost găsită în documentele disponibile.",
    "ru": "В доступных документах информация не найдена.",
}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        device=getattr(app.state, "device", "unknown"),
        models_loaded=getattr(app.state, "models_loaded", False),
        chunk_count=getattr(app.state, "chunk_count", 0),
    )


@app.post("/api/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest) -> SearchResponse:
    pool: ConnectionPool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")

    try:
        res = await run_in_threadpool(
            retrieve,
            pool,
            req.query,
            lang=req.lang,
            k=req.k,
            rerank=req.rerank,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    formatted_results = []
    for c in res.items:
        pages = c.get("pages")
        page_val = pages[0] if (pages and isinstance(pages, list) and len(pages) > 0) else None
        matched_lines = [
            MatchedLine(
                line_id=m["line_id"],
                idx=m["idx"],
                text=m["text"],
                score=m.get("score"),
            )
            for m in c.get("matched_lines", [])
        ]
        formatted_results.append(
            SearchResultItem(
                chunk_id=c["chunk_id"],
                doc_id=c.get("doc_id", ""),
                citation_label=c.get("citation_label", ""),
                text=c.get("text", ""),
                url=c.get("url", ""),
                found_on=c.get("found_on"),
                site=c.get("site"),
                lang=c.get("lang"),
                page=page_val,
                parent_legal_path=c.get("parent_legal_path"),
                rerank_score=c.get("rerank_score"),
                vec_rank=c.get("vec_rank"),
                fts_rank=c.get("fts_rank"),
                matched_lines=matched_lines,
            )
        )

    return SearchResponse(
        results=formatted_results,
        timings_ms=SearchTimings(
            embed=res.timings_ms.get("embed", 0.0),
            vector_sql=res.timings_ms.get("vector_sql", 0.0),
            fts_sql=res.timings_ms.get("fts_sql", 0.0),
            rerank=res.timings_ms.get("rerank", 0.0),
            total=res.timings_ms.get("total", 0.0),
        ),
        not_found=res.not_found,
    )


@app.get("/api/tools/schemas")
def tool_schemas() -> list[dict]:
    return TOOL_SCHEMAS


@app.post("/api/tools/search")
async def tool_search(req: ToolSearchRequest) -> dict:
    pool: ConnectionPool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")
    return await run_in_threadpool(
        search_tool, pool, req.query, lang=req.lang, site=req.site, k=req.k
    )


@app.post("/api/tools/grep")
async def tool_grep(req: ToolGrepRequest) -> dict:
    pool: ConnectionPool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")
    return await run_in_threadpool(
        grep_tool, pool, req.pattern, doc_id=req.doc_id, site=req.site, limit=req.limit
    )


@app.post("/api/tools/toc")
async def tool_toc(req: ToolTocRequest) -> dict:
    pool: ConnectionPool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")
    return await run_in_threadpool(toc_tool, pool, req.doc_id)


@app.post("/api/tools/open")
async def tool_open(req: ToolOpenRequest) -> dict:
    pool: ConnectionPool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")
    return await run_in_threadpool(
        open_tool,
        pool,
        req.doc_id,
        node_id=req.node_id,
        chunk_id=req.chunk_id,
        max_lines=req.max_lines,
    )


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    # Stub until retrieval over the offline_indexation corpus is wired in.
    lang = req.lang or "ro"
    return AskResponse(status="not_found", lang=lang, answer=NOT_FOUND[lang])
