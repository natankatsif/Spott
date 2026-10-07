"""Speed and quality eval (docs/history/tasks/10): eval/freshness.yaml + 15 RO and 15 RU questions from eval/lines.yaml.

    cd backend && uv run python scripts/eval_speed.py --model gpt-6-luna --label luna
    cd <older checkout>/backend && uv run python <this checkout>/backend/scripts/eval_speed.py --label before

Every question goes through answer_events() as /api/ask/stream serves it, one at a time. Measured:
- TTFT: from the request to the first answer word (delta event); total: to the done event;
- prompt tokens of the first answer call, share of questions with a second answer call, cost per 100 questions
  (answer + rewrite calls, list prices below);
- quality: newest document cited, expected fact in the answer, false contradictions (freshness set);
  gold line or chunk cited (lines set); share of sentences whose numbers are backed by their quotes.
Writes the per-question rows to --out (JSONL) and prints the summary row.

The organisation's tokens-per-minute limit (gpt-4o: 30,000) is kept by waiting *between* questions, so the
latency of each question is measured as a user would see it; a question that still hits 429 is retried.
"""

import argparse
import importlib
import json
import statistics
import time
from collections import deque
from pathlib import Path

import yaml

from spott.core.db import get_pool
from spott.core.paths import DATA_DIR, EVAL_DIR

EVAL = EVAL_DIR
# USD per 1M tokens (input, output), standard tier, https://developers.openai.com/api/docs/pricing (2026-09-26).
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00), "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
          "gpt-4.1-mini": (0.40, 1.60), "gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25)}


def price(model: str | None) -> tuple[float, float]:
    matches = [m for m in PRICES if model and model.startswith(m)]
    return PRICES[max(matches, key=len)] if matches else (0.0, 0.0)


def cases(per_lang: int) -> list[dict]:
    out = [c | {"set": "freshness"} for c in yaml.safe_load((EVAL / "freshness.yaml").read_text(encoding="utf-8"))]
    lines = [q for q in yaml.safe_load((EVAL / "lines.yaml").read_text(encoding="utf-8"))
             if not q.get("is_negative") and not q.get("exclude_from_metric")]
    for lang in ("ro", "ru"):
        picked = [q for q in lines if q["query_lang"] == lang][:per_lang]
        out += [{"set": "lines", "id": q["id"], "question": q["query"], "lang": lang,
                 "gold_line_id": q["gold_line_id"], "gold_chunk_id": q["gold_chunk_id"]} for q in picked]
    return out


def score(case: dict, r, sentence_flags: list[bool]) -> dict:
    row = {"verified_sentences": sentence_flags}
    if case["set"] == "freshness":
        urls = [c.url for c in r.citations]
        forbidden = set(case.get("forbid", []))
        mention = case.get("expect_mention")
        row |= {
            "newest": any(frag in u for u in urls for frag in case["expect_newest"]),
            "false_conflict": r.conflict is not None and r.conflict.kind == "contradiction",
            "forbidden": r.status in forbidden or (r.conflict is not None and r.conflict.kind in forbidden),
            "fact": None if not mention else any(str(m).lower() in r.answer.lower() for m in mention),
        }
    else:
        cited_lines = {lid for c in r.citations for lid in c.line_ids}
        row |= {"gold": case["gold_line_id"] in cited_lines or case["gold_chunk_id"] in {c.chunk_id for c in r.citations},
                "answered": r.status in ("answered", "partial", "conflict")}
    return row


def pct(values: list) -> str:
    values = [v for v in values if v is not None]
    return f"{100 * sum(values) / len(values):.0f}% ({sum(values)}/{len(values)})" if values else "—"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))] if ordered else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="answer model (default: OPENAI_MODEL)")
    ap.add_argument("--label", default="after")
    ap.add_argument("--per-lang", type=int, default=15)
    ap.add_argument("--only", choices=["freshness", "lines"])
    ap.add_argument("--ids", nargs="*", help="only these case ids")
    ap.add_argument("--out", type=Path, help="JSONL of per-question rows (default: <data>/eval/<label>.jsonl)")
    ap.add_argument("--tpm", type=int, default=30_000, help="OpenAI tokens-per-minute limit to stay under")
    ap.add_argument("--resume", action="store_true", help="skip the questions already in --out, append the rest")
    args = ap.parse_args()

    # the spott installed in the environment this runs in: run it from an older checkout's backend/ for "before"
    answering = importlib.import_module("spott.api.answering")
    # answering is a package since its split; an older checkout has one module
    logged = importlib.import_module("spott.api.answering.pipeline") if hasattr(answering, "__path__") else answering
    llm_module = importlib.import_module("spott.api.llm")
    schemas = importlib.import_module("spott.api.schemas")
    store_module = importlib.import_module("spott.api.store")
    records: list[dict] = []
    logged.log_query = records.append  # the per-question log record: tokens, calls, path

    pool = get_pool(min_size=1, max_size=10)
    store, llm = store_module.PgStore(pool), llm_module.OpenAILLM(model=args.model)
    todo = [c for c in cases(args.per_lang) if args.only in (None, c["set"]) and (not args.ids or c["id"] in args.ids)]
    out = args.out or DATA_DIR / "eval" / f"{args.label}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] \
        if args.resume and out.exists() else []
    todo = [c for c in todo if c["id"] not in {r["id"] for r in rows}]
    spent: deque[tuple[float, int]] = deque()  # (time, tokens) of the last minute

    def wait_for_budget(reserve: int) -> None:
        while True:
            now = time.time()
            while spent and spent[0][0] < now - 60:
                spent.popleft()
            if sum(t for _, t in spent) + reserve <= 0.9 * args.tpm:
                return
            time.sleep(1)

    def run(req) -> tuple[float | None, float, list[bool], object]:
        for attempt in range(4):
            wait_for_budget(max([r["tokens"] for r in rows] + [12_000]))
            t, ttft, flags, response = time.perf_counter(), None, [], None
            try:
                for event in answering.answer_events(store, llm, req, pool=pool):
                    if event["type"] == "delta" and ttft is None:
                        ttft = (time.perf_counter() - t) * 1000
                    elif event["type"] == "sentence":
                        flags.append(event["verified"])
                    elif event["type"] == "done":
                        response = schemas.AskResponse.model_validate(event["response"])
                return ttft, (time.perf_counter() - t) * 1000, flags, response
            except llm_module.LLMUnavailable as e:
                limited, offline = "429" in str(e), "Connection" in str(e)
                if not (limited or offline) or attempt == 3:
                    raise
                print(f"  {'rate limited' if limited else 'network down'}, waiting 30 s ({e})"[:160], flush=True)
                if limited:
                    spent.append((time.time(), args.tpm))  # the window is full
                time.sleep(30)
        raise RuntimeError("unreachable")

    try:
        answering.answer_question(store, llm, schemas.AskRequest(question="Ce este PUG?"), pool=pool)  # warm-up
        for case in todo:
            ttft, total, flags, response = run(schemas.AskRequest(question=case["question"], lang=case["lang"]))
            rec = records[-1]
            tokens = (rec["prompt_tokens"] + rec["completion_tokens"] + rec.get("rewrite_prompt_tokens", 0)
                      + rec.get("rewrite_completion_tokens", 0))
            spent.append((time.time(), tokens))
            p_in, p_out = price(rec.get("model"))
            w_in, w_out = price(rec.get("rewrite_model"))
            cost = (rec["prompt_tokens"] * p_in + rec["completion_tokens"] * p_out
                    + rec.get("rewrite_prompt_tokens", 0) * w_in + rec.get("rewrite_completion_tokens", 0) * w_out) / 1e6
            row = score(case, response, flags) | {
                "id": case["id"], "set": case["set"], "status": response.status, "path": response.meta.path,
                "ttft_ms": ttft or total, "total_ms": total, "llm_calls": rec.get("llm_calls", 0),
                "prompt_tokens_first": rec.get("prompt_tokens_first") or rec["prompt_tokens"] // max(1, rec.get("llm_calls", 1)),
                "cost_usd": cost, "tokens": tokens, "model": rec.get("model"), "gather_ms": rec.get("gather_ms"),
                "answer": response.answer,
            }
            rows.append(row)
            with out.open("a" if len(rows) > 1 or args.resume else "w", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{case['id']:24} {response.status:9} {response.meta.path:5} ttft={row['ttft_ms']:6.0f} "
                  f"total={total:6.0f} tok={row['prompt_tokens_first']:5} calls={row['llm_calls']} "
                  f"{'newest=' + str(row.get('newest')) if case['set'] == 'freshness' else 'gold=' + str(row['gold'])} "
                  f"| {response.answer[:80]}", flush=True)
    finally:
        pool.close()

    fr = [r for r in rows if r["set"] == "freshness"]
    ln = [r for r in rows if r["set"] == "lines"]
    ttft, total = [r["ttft_ms"] for r in rows], [r["total_ms"] for r in rows]
    flags = [f for r in rows for f in r["verified_sentences"]]
    print(f"\n| {args.label} | TTFT p50/p95 ms | total p50/p95 ms | prompt tokens p50 | 2nd LLM call | newest doc | "
          f"expected fact | false contradiction | gold cited (lines) | verified sentences | $ / 100 questions |")
    print(f"| {llm.model} | {statistics.median(ttft):.0f} / {percentile(ttft, 0.95):.0f} | "
          f"{statistics.median(total):.0f} / {percentile(total, 0.95):.0f} | "
          f"{statistics.median(r['prompt_tokens_first'] for r in rows):.0f} | "
          f"{pct([r['llm_calls'] > 1 for r in rows])} | {pct([r['newest'] for r in fr])} | "
          f"{pct([r['fact'] for r in fr])} | {pct([r['false_conflict'] for r in fr])} | {pct([r['gold'] for r in ln])} | "
          f"{pct(flags)} | {100 * statistics.mean(r['cost_usd'] for r in rows):.3f} |")


if __name__ == "__main__":
    main()
