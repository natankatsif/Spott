"""Query rewrite eval (docs/tasks/10 §5): hit@5 on the cross-language pairs of eval/lines.yaml, before/after.

    cd backend && uv run python scripts/eval_rewrite.py [--same]

before: the question's own search (retrieve). after: the question fused with its Romanian rewrite (and the
keyword lines), for several weights of a non-Romanian question in the fusion. The rewrite runs once per question.
Hits: gold chunk among the top 5 chunks; gold line among the matched lines of the top 5 chunks.
"""

import argparse
import sys
from pathlib import Path

import yaml
from retrieval import TOP_CANDIDATES, get_pool, retrieve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import answering  # noqa: E402
from app.llm import OpenAILLM  # noqa: E402
from app.schemas import AskRequest  # noqa: E402
from app.store import PgStore  # noqa: E402

EVAL = Path(__file__).resolve().parents[2] / "offline_indexation" / "eval" / "lines.yaml"
WEIGHTS = [1.0, 0.5, 0.25]


def hits(chunks: list[dict], focus: dict[str, list[str]], case: dict) -> tuple[bool, bool]:
    top = chunks[:5]
    return (any(c["chunk_id"] == case["gold_chunk_id"] for c in top),
            any(case["gold_line_id"] in focus.get(c["chunk_id"], []) for c in top))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--same", action="store_true", help="the same-language pairs instead (a check the rewrite doesn't hurt)")
    args = ap.parse_args()
    cases = [q for q in yaml.safe_load(EVAL.read_text(encoding="utf-8"))
             if not q.get("is_negative") and bool(q.get("is_cross_lingual")) != args.same]
    pool = get_pool(min_size=1, max_size=10)
    store, llm = PgStore(pool), OpenAILLM()
    rewrites: dict[str, object] = {}
    original = answering.rewrite_query

    def cached(llm_, req):
        if req.question not in rewrites:
            rewrites[req.question] = original(llm_, req)
        return rewrites[req.question]

    answering.rewrite_query = cached
    table: dict[str, list[tuple[bool, bool]]] = {"before": []} | {f"after w={w}": [] for w in WEIGHTS}
    try:
        for case in cases:
            q = case["query"]
            lang = answering.detect_lang(q)
            result = retrieve(pool, q, k=TOP_CANDIDATES)
            table["before"].append(hits(answering.distinct(answering.complete(result.items, store)),
                                        answering.matched_lines(result.items), case))
            for w in WEIGHTS:
                answering.CROSS_LANG_WEIGHT = w
                g = answering.gather(store, llm, pool, retrieve, AskRequest(question=q), q, lang, fresh=False,
                                     rewrite=True)
                table[f"after w={w}"].append(hits(g.candidates, g.focus, case))
            print(case["id"], case["query_lang"], "→", case["line_lang"], {k: v[-1] for k, v in table.items()},
                  "|", (rewrites[q].data.get("ro") if rewrites.get(q) else None), flush=True)
    finally:
        pool.close()
    n = len(cases)
    print(f"\n{n} {'same' if args.same else 'cross'}-language questions")
    print("| search | chunk hit@5 | line hit@5 |")
    print("|---|---|---|")
    for name, rows in table.items():
        print(f"| {name} | {sum(c for c, _ in rows) / n:.0%} ({sum(c for c, _ in rows)}/{n}) | "
              f"{sum(li for _, li in rows) / n:.0%} ({sum(li for _, li in rows)}/{n}) |")


if __name__ == "__main__":
    main()
