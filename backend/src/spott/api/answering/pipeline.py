"""One question from start to finish: routing and search in parallel, the answer streamed from the model and checked
sentence by sentence, a second answer when newer acts turn up, contacts when the documents fall short.

answer_events() yields the SSE events of /api/ask/stream; answer_question() returns the final AskResponse.
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime

from spott.core.paths import DATA_DIR
from spott.core.retrieval import retrieve

from ..languages import LANGUAGE_NAMES, detect_lang
from ..llm import DEEP, LLM, LLMResult
from ..schemas import AnswerMeta, AskRequest, AskResponse, TraceStep
from .chunks import distinct
from .contacts import pick_contacts, with_contacts
from .corpus import Store, with_mentioned_dates
from .prompts import ANSWER_SCHEMA, SYSTEM_PROMPT
from .response import Built, ResponseBuilder
from .routing import ROUTE_STATUS, route_question
from .search import Gathered, freshness_candidates, gather, needs_second_pass, retrieval_query
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


def new_answer_id() -> str:
    return f"a_{uuid.uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Draft:
    """An answer before its finishing touches: what was built, over which chunks, and what the stream showed."""
    built: Built
    chunks: list[dict] = field(default_factory=list)  # the sources it was answered from
    live: LiveSentences | None = None  # the first answer, streamed while the model wrote it
    replaced: bool = False  # a second answer took the first one's place
    prompt: str = ""  # the first answer's prompt


class AnswerRun:
    """One question: what its steps share — the store and the model, the answer's id and language, the trace, the
    model calls, the timings."""

    def __init__(self, store: Store, llm: LLM, req: AskRequest, *, pool, retrieve_fn: Callable, fresh: bool):
        self.store, self.llm, self.req, self.pool, self.retrieve_fn = store, llm, req, pool, retrieve_fn
        self.fresh = fresh  # the freshness queries: before the answer, and a second round after an unsettled one
        self.started = time.perf_counter()
        self.answer_id = new_answer_id()
        self.lang = detect_lang(req.question, req.lang)
        self.trace: list[TraceStep] = []
        self.calls: list[LLMResult] = []  # the answer model's calls
        self.ttft_ms: float | None = None  # until the first piece of the answer was sent
        self.fresh_new: int | None = None  # chunks the second round found; None when it didn't run

    def elapsed_ms(self, since: float | None = None) -> float:
        return round((time.perf_counter() - (self.started if since is None else since)) * 1000, 1)

    def first_output(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self.elapsed_ms()

    def events(self, *, rewrite: bool, routing: bool, translate: bool,
               on_done: Callable[[AskRequest, AskResponse, dict], None] | None) -> Iterator[dict]:
        """Everything after the start event. Trace events go out only before the answer text (contract order);
        steps after it are in done's trace."""
        query = retrieval_query(self.req)
        search_started = time.perf_counter()
        # the search starts right away; the routing call runs alongside it and decides whether it is needed
        route_f = _BACKGROUND.submit(route_question, self.llm, self.req, self.lang) \
            if routing and self.llm is not None else None
        gather_f = _BACKGROUND.submit(gather, self.store, self.llm, self.pool, self.retrieve_fn, self.req, query,
                                      self.lang, fresh=self.fresh, rewrite=rewrite)
        route = self.route(route_f)
        if route and route["route"] != "search":
            gather_f.cancel()
            yield from self.routed_reply(route)
            return
        g = gather_f.result()
        self.trace += self.search_steps(g, search_started)
        for step in self.trace:
            yield {"type": "trace", "step": step.model_dump()}

        draft = yield from self.draft(g, query)
        response = self.finish(draft, g, translate)
        for event in self.unshown(draft):
            if event["type"] == "delta":
                self.first_output()
            yield event
        log_query(self.record(response, draft, g))
        if on_done:  # what a partial answer lacked, and the sites found but not used: for the admin's gaps
            cited_sites = {c.site for c in response.citations}
            on_done(self.req, response, {
                "missing": [m.strip() for m in (self.calls[-1].data.get("missing") or []) if m.strip()]
                if self.calls and response.status == "partial" else [],
                "retrieved_sites": sorted({c["site"] for c in g.chunks if c.get("site")} - cited_sites),
            })
        yield {"type": "done", "response": response.model_dump()}

    def route(self, route_f: Future | None) -> dict | None:
        """The router's decision; without a router, or when it is late, a greeting recognized by code."""
        route = None
        if route_f:
            try:
                route = route_f.result(timeout=ROUTE_TIMEOUT_S)
            except FutureTimeout:
                log.warning("routing timed out")
        if route is None and SMALL_TALK.match(self.req.question):  # no router: a greeting still isn't "not found"
            route = {"route": "chat", "reply": SMALL_TALK_ANSWER[self.lang], "options": [], "model": None}
        return route

    def routed_reply(self, route: dict) -> Iterator[dict]:
        """A greeting, an off-topic or a vague message: the router's reply, with the questions it suggests."""
        meta = AnswerMeta(model=route["model"], path="none", latency_ms=self.elapsed_ms(), verified=False)
        response = ResponseBuilder([], {}, self.lang, self.answer_id).fixed(ROUTE_STATUS[route["route"]], route["reply"],
                                                                            [], meta, [])
        built = Built(response.model_copy(update={"followups": route["options"]}))
        yield from stream_sentences(built)
        log_query({"ts": now_iso(), "id": self.answer_id, "question": self.req.question, "lang": self.lang,
                   "status": built.response.status, "route": route["route"], "path": "none",
                   "total_ms": meta.latency_ms})
        yield {"type": "done", "response": built.response.model_dump()}

    def search_steps(self, g: Gathered, since: float) -> list[TraceStep]:
        steps = [TraceStep(tool="search", input=" · ".join(g.queries), ms=self.elapsed_ms(since),
                           summary=SEARCH_SUMMARY[self.lang].format(chunks=len(g.chunks),
                                                                    docs=len({c["doc_id"] for c in g.chunks})))]
        if g.fresh is not None:
            steps.append(TraceStep(tool="search", input="later acts · newer acts · current state", ms=0.0,
                                   summary=FRESH_SUMMARY[self.lang].format(n=g.fresh)))
        return steps

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[self.lang])

    def draft(self, g: Gathered, query: str) -> Generator[dict, None, Draft]:
        """The model's answer over the gathered sources, streamed as it writes; replaced by a second answer when it
        isn't settled and newer acts turn up. Nothing found: the not_found answer."""
        if g.result.not_found or not g.chunks:
            meta = AnswerMeta(model=None, path="none", latency_ms=0, verified=True)
            return Draft(Built(ResponseBuilder([], {}, self.lang, self.answer_id).not_found(g.chunks, meta, self.trace)))
        chunks, sources, docs = prepare_sources(g.chunks, self.store, g.focus, g.by_date)
        prompt = render_prompt(self.req, sources)
        builder = ResponseBuilder(sources, docs, self.lang, self.answer_id)
        live = LiveSentences(builder)
        stream = self.llm.stream_json(self.system_prompt(), prompt, "answer", ANSWER_SCHEMA,
                                      model=answer_model(self.req))
        while True:
            try:
                piece = next(stream)
            except StopIteration as stop:
                self.calls.append(stop.value)
                break
            for event in live.feed(piece):
                self.first_output()
                yield event
        meta = AnswerMeta(model=self.calls[-1].model, path="fast", latency_ms=0, verified=True)
        draft = Draft(builder.build(self.calls[-1].data, chunks, meta, self.trace), chunks, live, prompt=prompt)
        if self.fresh and needs_second_pass(self.calls[-1].data):
            self.second_pass(draft, g, query)
        if draft.built.response.citations:
            checked = draft.built.sentence_verified
            self.trace.append(TraceStep(tool="verify", input="", ms=0.0,
                                        summary=VERIFY_SUMMARY[self.lang].format(ok=sum(checked), total=len(checked))))
        return draft

    def second_pass(self, draft: Draft, g: Gathered, query: str) -> None:
        """A partial answer or a conflict: one more round for later and newer acts. If it finds any, an answer over
        them, newest first, replaces the first one; else the first answer stands."""
        started = time.perf_counter()
        cited_ids = {c.chunk_id for c in draft.built.response.citations}
        cited = [c for c in draft.chunks if c["chunk_id"] in cited_ids]
        new, searched = freshness_candidates(self.store, self.pool, self.retrieve_fn, query, self.lang, cited,
                                             draft.chunks, self.calls[-1].data.get("search_ro") or "")
        self.fresh_new = len(new)
        self.trace.append(TraceStep(tool="search", input=searched, ms=self.elapsed_ms(started),
                                    summary=FRESH_SUMMARY[self.lang].format(n=len(new))))
        if not new:
            return
        # The new sources first in the budget, then the ones the first answer cited, then the rest.
        rest = [c for c in draft.chunks if c["chunk_id"] not in cited_ids]
        merged = with_mentioned_dates(distinct(new + cited + rest), self.store)
        chunks, sources, docs = prepare_sources(merged, self.store, g.focus, by_date=True)
        self.calls.append(self.llm.complete_json(self.system_prompt(), render_prompt(self.req, sources), "answer",
                                                 ANSWER_SCHEMA, model=answer_model(self.req)))
        meta = AnswerMeta(model=self.calls[-1].model, path="agent", latency_ms=0, verified=True)
        draft.built = ResponseBuilder(sources, docs, self.lang, self.answer_id).build(self.calls[-1].data, chunks,
                                                                                      meta, self.trace)
        draft.chunks, draft.replaced = chunks, True

    def finish(self, draft: Draft, g: Gathered, translate: bool) -> AskResponse:
        """Untranslated quotes translated, contacts when the documents fall short, the trace and the latency."""
        response = translate_missing(self.llm, draft.built.response) if translate else draft.built.response
        if response.status in ("not_found", "partial") and not response.contacts:
            unused_sites = {c["site"] for c in g.chunks if c.get("site")} - {c.site for c in response.citations}
            if contacts := pick_contacts(self.store, self.req.question, self.lang, unused_sites):
                response = with_contacts(response, contacts)
        response = response.model_copy(update={
            "trace": self.trace,
            "meta": response.meta.model_copy(update={"latency_ms": self.elapsed_ms()}),
        })
        draft.built.response = response
        return response

    @staticmethod
    def unshown(draft: Draft) -> Iterator[dict]:
        """What the live stream didn't show: a second answer (whole if nothing was shown yet, else it arrives with
        done), missing parts, the not_found and refused texts."""
        if draft.replaced:
            return stream_sentences(draft.built) if not (draft.live and draft.live.emitted) else iter(())
        if draft.live:
            return stream_sentences(draft.built, start=draft.live.emitted, sent=draft.live.sent)
        return stream_sentences(draft.built)

    def record(self, response: AskResponse, draft: Draft, g: Gathered) -> dict:
        """The question's line in the query log."""
        rw, calls = g.rewrite, self.calls
        return {
            "ts": now_iso(),
            "id": self.answer_id,
            "question": self.req.question,
            "lang": self.lang,
            "status": response.status,
            "path": response.meta.path,
            "verified": response.meta.verified,
            "retrieved": [c["chunk_id"] for c in g.chunks],
            "cited_lines": [lid for c in response.citations for lid in c.line_ids],
            "dropped_sentences": draft.built.dropped,
            "rewrite": rw.data if rw else None,
            "fresh_added": g.fresh,
            "freshness_new_chunks": self.fresh_new,
            "verdicts": [c.data.get("verdict") for c in calls],
            "model": calls[-1].model if calls else None,
            "llm_calls": len(calls),
            "prompt_chars": len(draft.prompt),
            "prompt_tokens_first": calls[0].prompt_tokens if calls else 0,
            "prompt_tokens": sum(c.prompt_tokens for c in calls),
            "completion_tokens": sum(c.completion_tokens for c in calls),
            "rewrite_model": rw.model if rw else None,
            "rewrite_prompt_tokens": rw.prompt_tokens if rw else 0,
            "rewrite_completion_tokens": rw.completion_tokens if rw else 0,
            "retrieval_ms": g.result.timings_ms.get("total"),
            "gather_ms": g.timings_ms,
            "ttft_ms": self.ttft_ms,
            "total_ms": response.meta.latency_ms,
        }


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
    """SSE events for one question. Raises LLMUnavailable if the model can't be reached."""
    run = AnswerRun(store, llm, req, pool=pool, retrieve_fn=retrieve_fn,
                    fresh=FRESHNESS_PASS if freshness is None else freshness)
    yield {"type": "start", "id": run.answer_id, "lang": run.lang}
    yield from run.events(rewrite=QUERY_REWRITE if rewrite is None else rewrite,
                          routing=ROUTER if routing is None else routing, translate=translate, on_done=on_done)


def replay_events(cached: AskResponse, req: AskRequest,
                  on_done: Callable[[AskRequest, AskResponse, dict], None] | None = None) -> Iterator[dict]:
    """A quick question's checked answer replayed with the same events as a live one: a new answer id (ratings
    stay per answer), meta.path = "cache"."""
    started = time.perf_counter()
    answer_id = new_answer_id()
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
