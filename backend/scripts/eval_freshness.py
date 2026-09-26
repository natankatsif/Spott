"""Freshness eval (docs/tasks/08): the same questions answered without and with the freshness pass.

    cd backend && uv run python scripts/eval_freshness.py [--only fresh|fast] [--repeat N]

Needs the index in Postgres and OPENAI_API_KEY. Prints per-question results and the summary table:
newest-document hit rate, false conflicts, answers mentioning the expected fact, latency p50/p95.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import yaml
from retrieval import get_pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.answering import answer_question  # noqa: E402
from app.llm import OpenAILLM  # noqa: E402
from app.schemas import AskRequest  # noqa: E402
from app.store import PgStore  # noqa: E402

EVAL = Path(__file__).resolve().parents[2] / "offline_indexation" / "eval" / "freshness.yaml"


def check(case: dict, r) -> dict:
    urls = [c.url for c in r.citations]
    forbidden = set(case.get("forbid", []))
    bad = r.status in forbidden or (r.conflict is not None and r.conflict.kind in forbidden)
    mention = case.get("expect_mention")
    return {
        "newest": any(frag in u for u in urls for frag in case["expect_newest"]),
        "false_conflict": r.conflict is not None and r.conflict.kind == "contradiction" and "contradiction" in forbidden,
        "forbidden": bad,
        "mention": None if not mention else any(str(m).lower() in r.answer.lower() for m in mention),
        "lang_ok": r.lang == case["lang"],
    }


def pct(values: list[bool]) -> str:
    return f"{100 * sum(values) / len(values):.0f}% ({sum(values)}/{len(values)})" if values else "—"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["fast", "fresh"])
    ap.add_argument("--repeat", type=int, default=1, help="runs per question and mode (LLM answers vary)")
    args = ap.parse_args()

    cases = yaml.safe_load(EVAL.read_text(encoding="utf-8"))
    pool = get_pool(min_size=1, max_size=8)
    store, llm = PgStore(pool), OpenAILLM()
    modes = [m for m in ("fast", "fresh") if args.only in (None, m)]
    results: dict[str, list[dict]] = {m: [] for m in modes}
    try:
        for case in cases:
            for mode in modes:
                for _ in range(args.repeat):
                    t = time.perf_counter()
                    r = answer_question(store, llm, AskRequest(question=case["question"], lang=case["lang"]),
                                        pool=pool, freshness=mode == "fresh")
                    row = check(case, r) | {"id": case["id"], "ms": (time.perf_counter() - t) * 1000,
                                            "status": r.status, "path": r.meta.path,
                                            "conflict": r.conflict.kind if r.conflict else None}
                    results[mode].append(row)
                    print(f"{mode:5} {case['id']:24} {r.status:9} {r.meta.path:5} {row['ms']:6.0f} ms  "
                          f"newest={'✓' if row['newest'] else '✗'} mention={row['mention']} "
                          f"conflict={row['conflict']} | {r.answer[:90]}")
    finally:
        pool.close()

    print(f"\n{'mode':6} {'newest doc cited':18} {'false contradiction':20} {'forbidden status':18} "
          f"{'expected fact':16} {'p50 ms':>7} {'p95 ms':>7}")
    for mode, rows in results.items():
        ms = [r["ms"] for r in rows]
        print(f"{mode:6} {pct([r['newest'] for r in rows]):18} {pct([r['false_conflict'] for r in rows]):20} "
              f"{pct([r['forbidden'] for r in rows]):18} {pct([r['mention'] for r in rows if r['mention'] is not None]):16} "
              f"{statistics.median(ms):7.0f} {percentile(ms, 0.95):7.0f}")


if __name__ == "__main__":
    main()
