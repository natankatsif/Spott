"""Municipal assistant API.

    uv run uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool import ConnectionPool
from retrieval import (
    NOT_FOUND_THRESHOLD,
    TOP_CANDIDATES,
    HybridSearcher,
    build_fts_query,
    deduplicate_results,
    execute_fts_query,
    execute_vector_query,
    get_device,
    get_embedding_model,
    get_pool,
    get_reranker_model,
    rerank_candidates,
    rrf_fuse,
)
from starlette.concurrency import run_in_threadpool

from .schemas import (
    AskRequest,
    AskResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchTimings,
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
    log.info("Loading reranker model on %s...", device)
    await run_in_threadpool(get_reranker_model, device)
    app.state.models_loaded = True

    # 3. Warm-up search query to compile MPS/CUDA kernels
    log.info("Running warm-up search query...")
    searcher = HybridSearcher()
    try:
        await run_in_threadpool(searcher.search_rerank, "warmup query", k=8, top_candidates=30)
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

    device = getattr(app.state, "device", None)
    t_start = time.perf_counter()

    # 1. Embed query (runs in worker thread)
    searcher = HybridSearcher()
    t_embed_start = time.perf_counter()
    q_vec = await run_in_threadpool(searcher.embed_query, req.query)
    embed_ms = (time.perf_counter() - t_embed_start) * 1000.0

    # 2. Run vector and FTS SQL queries in parallel using pool connections
    fts_query = build_fts_query(req.query)

    def run_vector() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with pool.connection() as conn:
            rows = execute_vector_query(conn, q_vec, lang=req.lang, limit=TOP_CANDIDATES)
        return rows, (time.perf_counter() - t0) * 1000.0

    def run_fts() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with pool.connection() as conn:
            rows = execute_fts_query(conn, fts_query, lang=req.lang, limit=TOP_CANDIDATES)
        return rows, (time.perf_counter() - t0) * 1000.0

    (vec_res, vec_ms), (fts_res, fts_ms) = await asyncio.gather(
        run_in_threadpool(run_vector),
        run_in_threadpool(run_fts),
    )

    # 3. RRF Fusion + Deduplication
    fused = rrf_fuse(vec_res, fts_res)
    candidates = deduplicate_results(fused, k=TOP_CANDIDATES)

    # 4. Rerank top candidates with CrossEncoder (if requested)
    rerank_ms = 0.0
    if req.rerank and candidates:
        t_rerank_start = time.perf_counter()
        candidates = await run_in_threadpool(
            rerank_candidates,
            req.query,
            candidates,
            top_k=req.k,
            device=device,
        )
        rerank_ms = (time.perf_counter() - t_rerank_start) * 1000.0
    else:
        candidates = candidates[: req.k]

    total_ms = (time.perf_counter() - t_start) * 1000.0

    # 5. Check rejection threshold
    not_found = False
    if not candidates:
        not_found = True
    elif req.rerank:
        top_score = candidates[0].get("rerank_score", 0.0)
        if top_score < NOT_FOUND_THRESHOLD:
            not_found = True

    # 6. Format response items
    formatted_results = []
    for c in candidates:
        pages = c.get("pages")
        page_val = pages[0] if (pages and isinstance(pages, list) and len(pages) > 0) else None
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
            )
        )

    return SearchResponse(
        results=formatted_results,
        timings_ms=SearchTimings(
            embed=round(embed_ms, 2),
            vector_sql=round(vec_ms, 2),
            fts_sql=round(fts_ms, 2),
            rerank=round(rerank_ms, 2),
            total=round(total_ms, 2),
        ),
        not_found=not_found,
    )


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    # Stub until retrieval over the offline_indexation corpus is wired in.
    lang = req.lang or "ro"
    return AskResponse(status="not_found", lang=lang, answer=NOT_FOUND[lang])
