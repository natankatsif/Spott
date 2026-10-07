"""One question from start to finish: routing and search in parallel, the answer streamed from the model and checked
sentence by sentence, a second answer when newer acts turn up, contacts when the documents fall short.

answer_events() yields the SSE events of /api/ask/stream; answer_question() returns the final AskResponse.
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime

from spott.core.paths import DATA_DIR
from spott.core.pipeline import retrieve

from ..languages import LANGUAGE_NAMES, detect_lang
from ..llm import DEEP, LLM, LLMResult
from ..schemas import AnswerMeta, AskRequest, AskResponse, TraceStep
from .chunks import distinct
from .contacts import pick_contacts, with_contacts
from .corpus import Store, with_mentioned_dates
from .prompts import ANSWER_SCHEMA, SYSTEM_PROMPT
from .response import Built, ResponseBuilder
from .routing import ROUTE_STATUS, route_question
from .search import freshness_candidates, gather, needs_second_pass, retrieval_query
from .sources import prepare_sources, render_prompt
from .streaming import LiveSentences, stream_sentences
from .texts import FRESH_SUMMARY, SEARCH_SUMMARY, SMALL_TALK, SMALL_TALK_ANSWER, VERIFY_SUMMARY
from .translation import translate_missing

log = logging.getLogger("backend.answering")
FRESHNESS_PASS = os.getenv("FRESHNESS_PASS", "true").lower() in ("1", "true", "yes")
QUERY_REWRITE = os.getenv("QUERY_REWRITE", "true").lower() in ("1", "true", "yes")
QUERY_LOG_DIR = DATA_DIR / "query_logs"
ROUTE_TIMEOUT_S = float(os.getenv("ROUTE_TIMEOUT_S", "6"))
ROUTER = os.getenv("ROUTER", "true").lower() in ("1", "true", "yes")
# Each question holds two of these (routing and gathering) while it waits on the network and the database, so the
# pool is sized for many questions at once (a QR stand), not for the CPU.
_BACKGROUND = ThreadPoolExecutor(max_workers=int(os.getenv("ASK_THREADS", "64")), thread_name_prefix="ask")


def log_query(record: dict) -> None:
    """One JSON line per question: for gap analysis, eval and the budget (tokens per question)."""
    try:
        QUERY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (QUERY_LOG_DIR / f"{datetime.now(UTC):%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("query log not written: %s", e)


def answer_model(req: AskRequest) -> str | None:
    return DEEP if req.mode == "deep" else None


def answer_events(
    store: Store,
    llm: LLM,
    req: AskRequest,
    *,
    pool=None,
    retrieve_fn: Callable = retrieve,
    on_done: Callable[[AskRequest, AskResponse, dict], None] | None = None,
    freshness: bool | None = None,
    rewrite: bool | None = None,
    routing: bool | None = None,
    translate: bool = True,
) -> Iterator[dict]:
    """SSE events for one question. Raises LLMUnavailable if the model can't be reached.

    Trace events go out only before the answer text (contract order); steps after it are in done's trace."""
    started = time.perf_counter()
    answer_id = f"a_{uuid.uuid4().hex[:16]}"
    lang = detect_lang(req.question, req.lang)
    fresh = FRESHNESS_PASS if freshness is None else freshness
    yield {"type": "start", "id": answer_id, "lang": lang}

    query = retrieval_query(req)
    t = time.perf_counter()
    # the search starts right away; the routing call runs alongside it and decides whether it is needed
    use_router = ROUTER if routing is None else routing
    route_f = _BACKGROUND.submit(route_question, llm, req, lang) if use_router and llm is not None else None
    gather_f = _BACKGROUND.submit(gather, store, llm, pool, retrieve_fn, req, query, lang, fresh=fresh,
                                  rewrite=QUERY_REWRITE if rewrite is None else rewrite)
    route = None
    if route_f:
        try:
            route = route_f.result(timeout=ROUTE_TIMEOUT_S)
        except FutureTimeout:
            log.warning("routing timed out")
    if route is None and SMALL_TALK.match(req.question):  # no router: a greeting still isn't "not found"
        route = {"route": "chat", "reply": SMALL_TALK_ANSWER[lang], "options": [], "model": None}
    if route and route["route"] != "search":
        gather_f.cancel()
        meta = AnswerMeta(model=route["model"], path="none",
                          latency_ms=round((time.perf_counter() - started) * 1000, 1), verified=False)
        response = ResponseBuilder([], {}, lang, answer_id).fixed(ROUTE_STATUS[route["route"]], route["reply"], [],
                                                                   meta, [])
        built = Built(response.model_copy(update={"followups": route["options"]}))
        yield from stream_sentences(built)
        log_query({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "id": answer_id, "question": req.question,
                   "lang": lang, "status": built.response.status, "route": route["route"], "path": "none",
                   "total_ms": meta.latency_ms})
        yield {"type": "done", "response": built.response.model_dump()}
        return
    g = gather_f.result()
    search = TraceStep(tool="search", input=" · ".join(g.queries), ms=round((time.perf_counter() - t) * 1000, 1),
                       summary=SEARCH_SUMMARY[lang].format(chunks=len(g.chunks),
                                                           docs=len({c["doc_id"] for c in g.chunks})))
    trace = [search]
    if g.fresh is not None:
        trace.append(TraceStep(tool="search", input="later acts · newer acts · current state", ms=0.0,
                               summary=FRESH_SUMMARY[lang].format(n=g.fresh)))
    for step in trace:
        yield {"type": "trace", "step": step.model_dump()}

    calls: list[LLMResult] = []
    ttft_ms = None
    fresh_new: int | None = None
    live = None
    replaced = False
    prompt = ""
    if g.result.not_found or not g.chunks:
        meta = AnswerMeta(model=None, path="none", latency_ms=0, verified=True)
        built = Built(ResponseBuilder([], {}, lang, answer_id).not_found(g.chunks, meta, trace))
    else:
        system = SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[lang])
        chunks, sources, docs = prepare_sources(g.chunks, store, g.focus, g.by_date)
        prompt = render_prompt(req, sources)
        builder = ResponseBuilder(sources, docs, lang, answer_id)
        live = LiveSentences(builder)
        stream = llm.stream_json(system, prompt, "answer", ANSWER_SCHEMA, model=answer_model(req))
        while True:
            try:
                piece = next(stream)
            except StopIteration as stop:
                calls.append(stop.value)
                break
            for event in live.feed(piece):
                if ttft_ms is None:
                    ttft_ms = round((time.perf_counter() - started) * 1000, 1)
                yield event
        meta = AnswerMeta(model=calls[-1].model, path="fast", latency_ms=0, verified=True)
        built = builder.build(calls[-1].data, chunks, meta, trace)

        if fresh and needs_second_pass(calls[-1].data):
            t = time.perf_counter()
            cited_ids = {cit.chunk_id for cit in built.response.citations}
            new, searched = freshness_candidates(store, pool, retrieve_fn, query, lang,
                                                 [c for c in chunks if c["chunk_id"] in cited_ids], chunks,
                                                 calls[-1].data.get("search_ro") or "")
            fresh_new = len(new)
            trace.append(TraceStep(tool="search", input=searched, ms=round((time.perf_counter() - t) * 1000, 1),
                                   summary=FRESH_SUMMARY[lang].format(n=len(new))))
            if new:  # re-answer over sources sorted newest first; nothing new → keep the first answer
                # The new sources first in the budget, then the ones the first answer cited, then the rest.
                cited_first = [c for c in chunks if c["chunk_id"] in cited_ids] + \
                              [c for c in chunks if c["chunk_id"] not in cited_ids]
                merged = with_mentioned_dates(distinct(new + cited_first), store)
                chunks, sources, docs = prepare_sources(merged, store, g.focus, by_date=True)
                calls.append(llm.complete_json(system, render_prompt(req, sources), "answer", ANSWER_SCHEMA,
                                               model=answer_model(req)))
                meta = AnswerMeta(model=calls[-1].model, path="agent", latency_ms=0, verified=True)
                built = ResponseBuilder(sources, docs, lang, answer_id).build(calls[-1].data, chunks, meta, trace)
                replaced = True

        if built.response.citations:
            checked = built.sentence_verified
            trace.append(TraceStep(tool="verify", input="", ms=0.0,
                                   summary=VERIFY_SUMMARY[lang].format(ok=sum(checked), total=len(checked))))

    response = translate_missing(llm, built.response) if translate else built.response
    if response.status in ("not_found", "partial") and not response.contacts:
        cited_sites = {c.site for c in response.citations}
        contacts = pick_contacts(store, req.question, lang, {c["site"] for c in g.chunks if c.get("site")} - cited_sites)
        if contacts:
            response = with_contacts(response, contacts)
    response = response.model_copy(update={
        "trace": trace,
        "meta": response.meta.model_copy(update={"latency_ms": round((time.perf_counter() - started) * 1000, 1)}),
    })
    built.response = response
    if replaced:  # the second answer: streamed whole if nothing was shown yet, else it arrives with done
        tail = stream_sentences(built) if not (live and live.emitted) else iter(())
    else:  # what the model's stream didn't show: missing parts, not_found / refused texts
        tail = stream_sentences(built, start=live.emitted, sent=live.sent) if live else stream_sentences(built)
    for event in tail:
        if ttft_ms is None and event["type"] == "delta":
            ttft_ms = round((time.perf_counter() - started) * 1000, 1)
        yield event

    rw = g.rewrite
    log_query({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "id": answer_id,
        "question": req.question,
        "lang": lang,
        "status": response.status,
        "path": response.meta.path,
        "verified": response.meta.verified,
        "retrieved": [c["chunk_id"] for c in g.chunks],
        "cited_lines": [lid for c in response.citations for lid in c.line_ids],
        "dropped_sentences": built.dropped,
        "rewrite": rw.data if rw else None,
        "fresh_added": g.fresh,
        "freshness_new_chunks": fresh_new,
        "verdicts": [c.data.get("verdict") for c in calls],
        "model": calls[-1].model if calls else None,
        "llm_calls": len(calls),
        "prompt_chars": len(prompt),
        "prompt_tokens_first": calls[0].prompt_tokens if calls else 0,
        "prompt_tokens": sum(c.prompt_tokens for c in calls),
        "completion_tokens": sum(c.completion_tokens for c in calls),
        "rewrite_model": rw.model if rw else None,
        "rewrite_prompt_tokens": rw.prompt_tokens if rw else 0,
        "rewrite_completion_tokens": rw.completion_tokens if rw else 0,
        "retrieval_ms": g.result.timings_ms.get("total"),
        "gather_ms": g.timings_ms,
        "ttft_ms": ttft_ms,
        "total_ms": response.meta.latency_ms,
    })
    if on_done:  # what a partial answer lacked, and the sites found but not used: for the admin's gaps
        cited_sites = {c.site for c in response.citations}
        on_done(req, response, {
            "missing": [m.strip() for m in (calls[-1].data.get("missing") or []) if m.strip()]
            if calls and response.status == "partial" else [],
            "retrieved_sites": sorted({c["site"] for c in g.chunks if c.get("site")} - cited_sites),
        })
    yield {"type": "done", "response": response.model_dump()}


def replay_events(cached: AskResponse, req: AskRequest,
                  on_done: Callable[[AskRequest, AskResponse, dict], None] | None = None) -> Iterator[dict]:
    """A quick question's checked answer replayed with the same events as a live one: a new answer id (ratings
    stay per answer), meta.path = "cache"."""
    started = time.perf_counter()
    answer_id = f"a_{uuid.uuid4().hex[:16]}"
    yield {"type": "start", "id": answer_id, "lang": cached.lang}
    for step in cached.trace:
        yield {"type": "trace", "step": step.model_dump()}
    response = cached.model_copy(update={"id": answer_id, "meta": cached.meta.model_copy(update={"path": "cache"})})
    yield from stream_sentences(Built(response))
    response = response.model_copy(update={"meta": response.meta.model_copy(
        update={"latency_ms": round((time.perf_counter() - started) * 1000, 1)})})
    if on_done:
        on_done(req, response, {})
    yield {"type": "done", "response": response.model_dump()}


def answer_question(store: Store, llm: LLM, req: AskRequest, **kwargs) -> AskResponse:
    for event in answer_events(store, llm, req, **kwargs):
        if event["type"] == "done":
            return AskResponse.model_validate(event["response"])
    raise RuntimeError("answer stream ended without a done event")
