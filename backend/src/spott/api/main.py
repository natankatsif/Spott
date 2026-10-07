"""The API's composition root (contract: docs/API.md): the services built at startup and kept on app.state, the
routers (routes: the public API; admin: the admin panel), errors, CORS, the quick questions' background re-check.

    uv run uvicorn spott.api.main:app --reload --port 8000
"""

import asyncio
import functools
import logging
import mimetypes
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from spott.core.db import get_pool, init_app_db
from spott.core.embeddings import get_device, get_embedding_model
from spott.core.paths import SITES_TOML
from spott.core.retrieval import retrieve
from spott.core.sources import seed_sources

from . import admin, errors, preview, usage
from .admin.gaps import PgGaps, llm_cluster
from .admin.store import PgAdminStore
from .answering import answer_question
from .answers import PgAnswers
from .deps import llm
from .errors import RateLimiter
from .llm import LLM
from .llm_settings import LLMHolder, PgSettings
from .pdf_source import PdfSource, make_clients
from .routes import CORS_ORIGINS, IndexSize, recorder, router
from .store import PgStore
from .suggestions import PgSuggestions, llm_translate
from .visitors import PgVisitors

log = logging.getLogger("backend")

ASK_RATE_LIMIT = int(os.getenv("ASK_RATE_LIMIT", "10"))  # questions per minute per client
# Quick questions are re-asked (LLM calls) when the index changes and daily; checked every this many seconds.
SUGGESTIONS_EVERY_S = float(os.getenv("SUGGESTIONS_EVERY_S", "600"))
SUGGESTIONS_RECHECK = os.getenv("SUGGESTIONS_RECHECK", "true").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("Starting up Municipal Assistant API...")
    state = app.state
    device = get_device()
    state.device = device
    state.models_loaded = False
    state.rate_limiter = RateLimiter(limit=ASK_RATE_LIMIT)
    state.signal_limiter = RateLimiter(limit=20)  # outdated-content signals per client per minute

    pool = get_pool(min_size=2, max_size=10)
    state.pool = pool
    state.index_size = IndexSize()
    state.store = PgStore(pool)
    try:  # app-state tables (sources, jobs, answers, feedback, suggestions, contacts), sources seeded
        with pool.connection() as conn:
            init_app_db(conn)
            added = seed_sources(conn, SITES_TOML)
        if added:
            log.info("Sources seeded: %d added", added)
    except Exception as e:
        log.warning("App tables not created: %s", e)
    state.admin = PgAdminStore(pool)
    state.usage = usage.PgUsage(pool)
    state.llm_holder = LLMHolder(PgSettings(pool), on_usage=record_usage)
    state.answers = PgAnswers(pool)
    state.suggestions = PgSuggestions(pool)
    model = functools.partial(llm, state)  # the model client as the settings are when it is called
    state.translate = llm_translate(model)  # a pinned question into the other language
    state.gaps = PgGaps(pool, cluster=llm_cluster(model))
    state.visitors = PgVisitors(pool)
    # The admin's gap re-check: one question, one model call (no routing, rewrite or translation call, no second pass).
    state.ask_once = lambda req: answer_question(state.store, model(), req, pool=pool, freshness=False, rewrite=False,
                                                 routing=False, translate=False, on_done=recorder(state))
    http_clients = make_clients()
    state.http = http_clients[0]
    state.pdf_source = PdfSource(*http_clients)
    state.pages = preview.PageSource(http_clients[0], pool)

    log.info("Loading embedding model on %s...", device)
    await run_in_threadpool(get_embedding_model, device)
    state.models_loaded = True

    # Warm-up query compiles MPS/CUDA kernels before the first real question.
    try:
        await run_in_threadpool(retrieve, pool, "warmup query", k=2)
    except Exception as e:
        log.warning("Warmup search query encountered error (ignored): %s", e)
    log.info("Indexed chunks ready: %d", await run_in_threadpool(state.index_size.get, pool))

    recheck = asyncio.create_task(recheck_suggestions(state, model)) if SUGGESTIONS_RECHECK else None
    yield

    log.info("Shutting down Municipal Assistant API...")
    if recheck:
        recheck.cancel()
    for client in http_clients:
        await client.aclose()
    pool.close()


async def recheck_suggestions(state, model: Callable[[], LLM]) -> None:
    """Quick questions: candidates from the answers log (seeds while it is empty), each re-asked when the index
    changed or a day has passed; one that is no longer answered and verified is dropped."""
    while True:
        try:
            await run_in_threadpool(state.suggestions.refresh)
            result = await run_in_threadpool(state.suggestions.recheck, functools.partial(
                answer_question, state.store, model(), pool=state.pool))
            if result["checked"]:
                log.info("quick questions re-checked: %s", result)
        except Exception as e:
            log.warning("quick questions not re-checked: %s", e)
        await asyncio.sleep(SUGGESTIONS_EVERY_S)


def record_usage(provider: str, model: str, role: str, kind: str, result, ms: int) -> None:
    """Every routed model call's tokens, for admin → Spending."""
    log_ = getattr(app.state, "usage", None)
    if log_ is not None:
        log_.record(usage.Call(provider=provider, model=model, role=role, kind=kind,
                               input_tokens=result.prompt_tokens or 0, output_tokens=result.completion_tokens or 0,
                               ms=ms))


app = FastAPI(title="Chișinău Municipal Assistant", lifespan=lifespan)
errors.install(app)
mimetypes.add_type("text/javascript", ".mjs")  # pdf.js is ES modules: a module script needs a JS type
app.mount(preview.STATIC_PREFIX, StaticFiles(directory=preview.STATIC), name="preview-static")
for admin_router in admin.ROUTERS:
    app.include_router(admin_router)
app.include_router(router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # CORS_ORIGINS=* for the widget embedded on other sites
    allow_methods=["*"],
    allow_headers=["*"],
)
