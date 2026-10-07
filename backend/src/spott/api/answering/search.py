"""Finding what to answer from, in two parallel rounds: the question's search while a small model rewrites it; then the
rewritten queries, the keyword lines and newer acts on the topic, fused with it. After an unsettled first answer,
freshness_candidates() runs one more round."""

import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from spott.core.config import TOP_CANDIDATES
from spott.core.pipeline import RetrievalResult

from ..llm import FAST, LLM, LLMResult, LLMUnavailable
from ..schemas import AskRequest
from .chunks import distinct, is_act, is_roster, newest_first, ro_date
from .corpus import Store, add_continuations, complete, with_mentioned_dates
from .prompts import REWRITE_PROMPT, REWRITE_SCHEMA, conversation, today_line

log = logging.getLogger("backend.answering")

TOP_CHUNKS = 12
FRESH_RANK = 6  # newer acts come right after the 6 most relevant chunks in the budget order
REWRITE_TIMEOUT_S = 3.0  # counted from the start of the search; a slower rewrite is ignored
REWRITE_MAX_TOKENS = 150
FRESHNESS_TIMEOUT_S = 1.5
FRESH_PER_QUERY = 6
FRESH_MAX = 4  # sources the freshness queries may add to the first prompt
FRESH_TAKE = {"later": 3, "newer": 2, "fresh_terms": 1}  # the best ones of each freshness query
FRESH_MODEL_QUERY_K = 8  # the model's own query is the most precise of the three
# Weights in the fused ranking (scripts/eval_rewrite.py): a question not in Romanian counts less than its Romanian
# rewrite, since the documents are mostly Romanian and cross-language search ranks them worse; the Russian
# rewrite of a Romanian question only adds the few Russian documents, so it counts less still.
CROSS_LANG_WEIGHT = float(os.getenv("CROSS_LANG_WEIGHT", "0.5"))
RU_REWRITE_WEIGHT = 0.25
GREP_MAX_LINES = 30  # a keyword found in more lines than this is too common to point anywhere
# Said by code, not by the model, when a cited act ended another one (or was ended) and the answer left it out.
# Added to the question to reach documents about the current state, which rarely reuse its wording.
FRESH_TERMS = {"ro": "reactualizare modificare abrogare în vigoare actual",
               "ru": "reactualizare modificare abrogare în vigoare обновление изменение отмена действующий",
               "en": "reactualizare modificare abrogare în vigoare actual"}
# "Who / which / when" questions ask about the current state.
NOW_QUESTION = re.compile(r"^\W*(cine|care|când|cand|кто|какой|какая|какие|каков\w*|когда|who|which|when)\b", re.I)
# "Who is in / members / composition / who chairs": the act that sets up the body lists the people with their
# roles; a regulation only describes the roles.
MEMBERS_QUESTION = re.compile(r"\b(membri\w*|componen\w*|cine (?:face|fac) parte|din cine|состав\w*|член\w*|"
                              r"кто входит|входят|președinte\w*|secretar\w*|председател\w*|секретар\w*)", re.I)
# Act numbers in a question ("decizia 4/1", "dispoziția 251-d"): searched verbatim in the lines.
ACT_NUMBER = re.compile(r"\b\d+(?:/\d+(?:-\d+)?|-[a-zа-я]{1,2})\b", re.I)


def retrieval_query(req: AskRequest) -> str:
    """A short follow-up ("а сколько это стоит?") is searched together with the previous question."""
    previous = [t.text for t in req.history if t.role == "user"]
    if previous and len(req.question) < 80:
        return f"{previous[-1][:300]} {req.question}"
    return req.question


def pick_chunks(candidates: list[dict]) -> list[dict]:
    """Top chunks, plus the newest-dated candidate even when it ranks below the cut-off."""
    top = candidates[:TOP_CHUNKS]
    newest = newest_first([c for c in candidates[TOP_CHUNKS:] if c.get("date")])[:1]
    top_dates = [c["date"] for c in top if c.get("date")]
    if newest and (not top_dates or newest[0]["date"] > max(top_dates)):
        top.append(newest[0])
    return top


def fuse(rankings: list[list[dict]], weights: list[float] | None = None, k: int = 60) -> list[dict]:
    """Weighted reciprocal rank fusion of result lists (the question, its RO and RU rewrites, keyword lines)."""
    scores: dict[str, float] = defaultdict(float)
    items: dict[str, dict] = {}
    for ranking, weight in zip(rankings, weights or [1.0] * len(rankings), strict=True):
        for rank, item in enumerate(ranking):
            scores[item["chunk_id"]] += weight / (k + rank + 1)
            items.setdefault(item["chunk_id"], item)
    return sorted(items.values(), key=lambda c: -scores[c["chunk_id"]])


def keyword_ranking(rows: list[dict], keywords: list[str]) -> tuple[list[dict], list[dict]]:
    """Chunks by how many distinct keywords their lines contain, and the matching lines. A keyword found in
    too many lines ("Chișinău") says nothing and is ignored."""
    hits: dict[str, list[dict]] = {k: [r for r in rows if k.casefold() in r["text"].casefold()] for k in keywords}
    useful = {k: found for k, found in hits.items() if 0 < len(found) <= GREP_MAX_LINES}
    per_chunk: dict[str, set[str]] = defaultdict(set)
    lines = []
    for k, found in useful.items():
        for r in found:
            per_chunk[r["chunk_id"]].add(k)
            lines.append(r)
    ranked = sorted(per_chunk, key=lambda cid: -len(per_chunk[cid]))
    return [{"chunk_id": cid} for cid in ranked], lines


def act_patterns(acts: list[dict]) -> list[str]:
    patterns = []
    for c in acts[:5]:
        n = c["number"]
        patterns += [f"nr. {n}", f"nr.{n}", f"nr {n}"] + ([f"{n} din {ro_date(c['date'])}"] if c.get("date") else [])
    return patterns


def run_parallel(executor: ThreadPoolExecutor, jobs: dict[str, Callable], timeout: float) -> dict:
    """Results of the jobs that finished in time; a slow or failed query is dropped, not waited for."""
    futures = {name: executor.submit(job) for name, job in jobs.items()}
    done, _ = wait(futures.values(), timeout=timeout)
    out = {}
    for name, f in futures.items():
        if f not in done:
            log.warning("%s query timed out", name)
        elif f.exception() is not None:
            log.warning("%s query failed: %s", name, f.exception())
        else:
            out[name] = f.result()
    return out


def needs_rewrite(req: AskRequest, lang: str) -> bool:
    """A Romanian question without a conversation is already a Romanian query: the rewrite call (~1 s, mostly
    network) found nothing more on the same-language pairs of eval/lines.yaml (scripts/eval_rewrite.py --same)."""
    return lang != "ro" or bool(req.history)


def rewrite_query(llm: LLM, req: AskRequest) -> LLMResult | None:
    """The question (and the conversation) as a Romanian and a Russian search query plus keywords."""
    user = today_line() + conversation(req) + f"Question: {req.question}"
    try:
        return llm.complete_json(REWRITE_PROMPT, user, "rewrite", REWRITE_SCHEMA, model=FAST, effort="none",
                                 max_tokens=REWRITE_MAX_TOKENS)
    except LLMUnavailable as e:
        log.warning("query rewrite failed: %s", e)
        return None


def add_focus(focus: dict[str, list[str]], chunk_id: str, line_ids: list[str], first: bool = False) -> None:
    """Matched lines of a chunk, best first; `first` puts exact keyword lines before the searches' matches."""
    known = focus.setdefault(chunk_id, [])
    new = [lid for lid in dict.fromkeys(line_ids) if lid and lid not in known]
    focus[chunk_id] = new + known if first else known + new


def matched_lines(items: list[dict], focus: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    focus = {} if focus is None else focus
    for item in items:
        add_focus(focus, item["chunk_id"], [line.get("line_id") for line in item.get("matched_lines") or []])
    return focus


@dataclass
class Gathered:
    chunks: list[dict]  # sources for the prompt, in priority order (the budget keeps the first ones)
    result: RetrievalResult  # the question's own search
    candidates: list[dict]  # all searches fused, before the cut (eval: hit@k)
    queries: list[str]  # what was searched, for the trace
    focus: dict[str, list[str]]  # chunk_id → ids of the lines the searches matched, best first
    fresh: int | None = None  # sources the freshness queries added; None when they didn't run
    by_date: bool = False  # newer acts were added: the prompt lists sources newest first
    rewrite: LLMResult | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


def gather(store: Store, llm: LLM, pool, retrieve_fn: Callable, req: AskRequest, query: str, lang: str, *,
           fresh: bool, rewrite: bool) -> Gathered:
    """Everything the first answer needs, in two parallel rounds (~0.2 s each, plus the rewrite call):
    1. the question's search, while a small model rewrites the question;
    2. the rewritten queries, the keyword lines and the freshness queries — later acts naming the found acts'
       numbers, acts dated after them on the same sites, the question with "current state" terms."""
    started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=8)
    try:
        main_f = executor.submit(retrieve_fn, pool, query, k=TOP_CANDIDATES)
        rewrite_f = executor.submit(rewrite_query, llm, req) if rewrite and needs_rewrite(req, lang) else None
        result = main_f.result()
        main = complete(result.items, store)
        timings = {"search": round((time.perf_counter() - started) * 1000, 1)}
        rw = None
        if rewrite_f:
            try:
                rw = rewrite_f.result(timeout=max(0.0, REWRITE_TIMEOUT_S - (time.perf_counter() - started)))
            except FutureTimeout:
                log.warning("query rewrite timed out")
            timings["rewrite"] = round((time.perf_counter() - started) * 1000, 1)
        ro = ru = ""
        keywords = list(dict.fromkeys(ACT_NUMBER.findall(req.question)))
        if rw:
            ro, ru = (rw.data.get("ro") or "").strip(), (rw.data.get("ru") or "").strip()
            keywords = list(dict.fromkeys(keywords + [k.strip() for k in rw.data.get("keywords") or []
                                                      if len(k.strip()) >= 3]))[:6]

        jobs: dict[str, Callable] = {}
        queries = [query]
        for name, q in (("ro", ro), ("ru", ru)):
            # The rewrite in the question's own language repeats it, unless it makes a follow-up standalone.
            if q and q.casefold() != query.casefold() and (name != lang or req.history):
                jobs[name] = lambda q=q: retrieve_fn(pool, q, k=TOP_CANDIDATES)
                queries.append(q)
        if keywords:
            jobs["grep"] = lambda: store.grep_lines(keywords)
        acts = [c for c in main[:TOP_CHUNKS] if is_act(c)]
        if fresh:
            search = ro or query
            terms = FRESH_TERMS["ro" if ro else lang]
            jobs["fresh_terms"] = lambda: retrieve_fn(pool, f"{search} {terms}", k=FRESH_PER_QUERY)
            if patterns := act_patterns(acts):
                exclude = list({c["doc_id"] for c in acts})
                jobs["later"] = lambda: store.later_acts(patterns, exclude, limit=FRESH_PER_QUERY)
            newest = max((c["date"] for c in acts if c.get("date")), default=None)
            sites = sorted({c["site"] for c in main[:TOP_CHUNKS] if c.get("site")}) or None
            if newest:
                jobs["newer"] = lambda: retrieve_fn(pool, search, k=FRESH_PER_QUERY, date_after=newest,
                                                    sites=sites)
        found = run_parallel(executor, jobs, FRESHNESS_TIMEOUT_S) if jobs else {}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    timings["round2"] = round((time.perf_counter() - started) * 1000, 1)

    focus = matched_lines(result.items)
    rankings, weights = [main], [1.0 if lang == "ro" or "ro" not in found else CROSS_LANG_WEIGHT]
    for name in ("ro", "ru"):
        if name in found:
            rankings.append(found[name].items)
            weights.append(1.0 if name == "ro" else RU_REWRITE_WEIGHT)
            matched_lines(found[name].items, focus)
    if "grep" in found:
        ranked, lines = keyword_ranking(found["grep"], keywords)
        rankings.append(ranked)
        weights.append(1.0)
        for line in lines:
            add_focus(focus, line["chunk_id"], [line["line_id"]], first=True)
    candidates = distinct(complete(fuse(rankings, weights), store))
    chunks = pick_chunks(candidates)
    if MEMBERS_QUESTION.search(f"{req.question} {ro}"):  # the list of people may be the chunk after the intro
        chunks = add_continuations(chunks, store)
        chunks = [c for c in chunks if is_roster(c)] + [c for c in chunks if not is_roster(c)]

    added, by_date = None, False
    if fresh:
        known = {c["chunk_id"] for c in chunks}
        rows = found.get("later", [])[:FRESH_TAKE["later"]]
        for line in rows:
            add_focus(focus, line["chunk_id"], [line["line_id"]])
        fresh_ids = [r["chunk_id"] for r in rows]
        for name in ("newer", "fresh_terms"):
            if name in found:
                items = [c for c in found[name].items if c["chunk_id"] not in known][:FRESH_TAKE[name]]
                fresh_ids += [c["chunk_id"] for c in items]
                matched_lines(items, focus)
        new_ids = [cid for cid in dict.fromkeys(fresh_ids) if cid not in known]
        meta = store.chunk_meta(new_ids) if new_ids else {}
        new = distinct([meta[cid] for cid in new_ids if cid in meta])[:FRESH_MAX]
        added = 0
        # Newer acts matter when the answer rests on acts, or the question asks about the current state.
        if new and (any(is_act(c) for c in chunks) or NOW_QUESTION.match(req.question)):
            merged = distinct(chunks[:FRESH_RANK] + new + chunks[FRESH_RANK:])
            added, by_date = len(merged) - len(chunks), True
            chunks = with_mentioned_dates(merged, store)
    return Gathered(chunks=chunks, result=result, candidates=candidates, queries=queries, focus=focus,
                    fresh=added, by_date=by_date, rewrite=rw, timings_ms=timings)


def needs_second_pass(data: dict) -> bool:
    """The first answer isn't settled: another answer is worth it only if newer sources turn up."""
    return data.get("verdict") == "partial" or (data.get("verdict") != "refused" and bool(data.get("conflict")))


def freshness_candidates(store: Store, pool, retrieve_fn: Callable, query: str, lang: str,
                         cited: list[dict], chunks: list[dict], model_query: str = "") -> tuple[list[dict], str]:
    """After an unsettled first answer, one bounded round, three queries in parallel: (a) lines naming the
    cited acts' numbers — later acts amend or cite them; (b) the model's Romanian query, or the question with
    "current state" terms; (c) acts dated after the newest cited act, on the same sites. Returns what is worth
    a second answer — (a), and acts newer than every cited act — and what was searched. Anything else found
    was as good as the first sources: the first answer stands."""
    acts = [c for c in cited if is_act(c)] or [c for c in chunks if is_act(c)]
    patterns = act_patterns(acts)
    newest = max((c["date"] for c in acts if c.get("date")), default=None)
    sites = sorted({c["site"] for c in (cited or chunks) if c.get("site")}) or None

    def ids(result) -> list[str]:
        return [c["chunk_id"] for c in result.items]

    search = model_query.strip() or query
    jobs: dict[str, Callable] = {}
    if model_query.strip():  # the documents' own nouns rank the right chunk higher than generic "current" terms
        jobs["model"] = lambda: ids(retrieve_fn(pool, search, k=FRESH_MODEL_QUERY_K))
    else:
        jobs["terms"] = lambda: ids(retrieve_fn(pool, f"{query} {FRESH_TERMS[lang]}", k=FRESH_PER_QUERY))
    if patterns:
        exclude = list({c["doc_id"] for c in acts})
        jobs["later"] = lambda: [r["chunk_id"] for r in store.later_acts(patterns, exclude, limit=FRESH_PER_QUERY)]
    if newest:
        jobs["newer"] = lambda: ids(retrieve_fn(pool, search, k=FRESH_PER_QUERY, date_after=newest,
                                                sites=sites))
    executor = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        found = run_parallel(executor, jobs, FRESHNESS_TIMEOUT_S)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    known = {c["chunk_id"] for c in chunks}
    new_ids = [cid for cid in dict.fromkeys(cid for ids_ in found.values() for cid in ids_) if cid not in known]
    meta = store.chunk_meta(new_ids) if new_ids else {}
    later = set(found.get("later", []))
    newer = [meta[cid] for cid in new_ids if cid in meta and (
        cid in later or (is_act(meta[cid]) and bool(meta[cid].get("date")) and meta[cid]["date"] > (newest or "")))]
    searched = " · ".join(filter(None, [
        search,
        f"nr. {', '.join(c['number'] for c in acts[:5])}" if patterns else "",
        f"> {newest}" if newest else "",
    ]))
    return newer, searched
