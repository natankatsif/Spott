"""Smoke evaluation for search quality across 4 search modes.

Evaluates:
  1. Vector only (BAAI/bge-m3 cosine similarity)
  2. FTS only (tsvector with ro_unaccent / ru_unaccent)
  3. Hybrid (Reciprocal Rank Fusion, k=60)
  4. CrossEncoder Reranker (BAAI/bge-reranker-v2-m3)

Metrics:
  - Hit@1, Hit@5
  - MRR (Mean Reciprocal Rank)
  - Avg Latency (ms)
  - Negative query score distribution vs Positive query score distribution (rejection threshold)
"""

from __future__ import annotations

import argparse
import logging
import statistics
from pathlib import Path
from typing import Any

import yaml
from retrieval.db import get_connection
from retrieval.pipeline import retrieve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("eval_smoke")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke search evaluation")
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "eval" / "search_smoke.yaml",
        help="Path to search_smoke.yaml",
    )
    p.add_argument(
        "--detail-queries",
        nargs="+",
        default=["ro-01", "cross-01", "cross-03", "neg-01"],
        help="Query IDs to print top-3 detailed results for",
    )
    return p.parse_args()


def load_queries(config_path: Path) -> list[dict[str, Any]]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_match(chunk: dict[str, Any], expected: Any) -> bool:
    if not expected or expected == "none":
        return False

    # 1. Site check (mandatory if specified)
    expected_site = expected.get("site")
    if expected_site:
        chunk_site = chunk.get("site") or ""
        chunk_url = chunk.get("url") or ""
        if expected_site.lower() not in chunk_site.lower() and expected_site.lower() not in chunk_url.lower():
            return False

    # 2. Doc_ids check (mandatory if specified)
    expected_doc_ids = expected.get("doc_ids")
    if expected_doc_ids:
        chunk_doc_id = chunk.get("doc_id") or ""
        chunk_id = chunk.get("chunk_id") or ""
        chunk_url = chunk.get("url") or ""
        matched_doc = any(
            did in chunk_doc_id
            or (chunk_doc_id and chunk_doc_id in did)
            or did in chunk_id
            or (chunk_url and did in chunk_url)
            for did in expected_doc_ids
        )
        if not matched_doc:
            return False

    # 3. Keywords check (optional secondary confirmation)
    keywords = expected.get("keywords", [])
    if keywords:
        full_text = (
            (chunk.get("text") or "")
            + " "
            + (chunk.get("citation_label") or "")
            + " "
            + (chunk.get("doc_type") or "")
            + " "
            + (chunk.get("title") or "")
        ).lower()
        if not any(kw.lower() in full_text for kw in keywords):
            return False

    return True


def evaluate_mode(
    conn: Any,
    mode_name: str,
    positive_queries: list[dict[str, Any]],
    negative_queries: list[dict[str, Any]],
) -> dict[str, Any]:
    latencies: list[float] = []
    hits_1: int = 0
    hits_5: int = 0
    reciprocal_ranks: list[float] = []
    pos_top1_scores: list[float] = []
    query_results: dict[str, list[dict[str, Any]]] = {}

    def run_query(query_text: str, k: int = 20) -> tuple[list[dict[str, Any]], float]:
        if mode_name == "Vector only":
            res = retrieve(conn, query_text, k=k, w_vector=1.0, w_fts=0.0, w_line=0.0, rerank=False)
        elif mode_name == "FTS only":
            res = retrieve(conn, query_text, k=k, w_vector=0.0, w_fts=1.0, w_line=0.0, rerank=False)
        elif mode_name == "Hybrid RRF":
            res = retrieve(conn, query_text, k=k, rerank=False)
        elif mode_name == "CrossEncoder Rerank":
            res = retrieve(conn, query_text, k=k, rerank=True)
        else:
            raise ValueError(f"Unknown mode: {mode_name}")
        return res.items, res.timings_ms.get("total", 0.0)

    for q in positive_queries:
        query_text = q["query"]
        expected = q.get("expected")

        results, elapsed_ms = run_query(query_text, k=20)
        latencies.append(elapsed_ms)
        query_results[q["id"]] = results

        if results and "rerank_score" in results[0]:
            pos_top1_scores.append(results[0]["rerank_score"])
        elif results and "score" in results[0]:
            pos_top1_scores.append(results[0]["score"])

        found_rank = 0
        for rank, r in enumerate(results[:20], 1):
            if is_match(r, expected):
                found_rank = rank
                break

        if found_rank == 1:
            hits_1 += 1
        if 1 <= found_rank <= 5:
            hits_5 += 1

        reciprocal_ranks.append(1.0 / found_rank if found_rank > 0 else 0.0)

    # Evaluate negative queries (out-of-corpus)
    neg_top1_scores: list[float] = []
    for q in negative_queries:
        query_text = q["query"]
        results, elapsed_ms = run_query(query_text, k=5)
        latencies.append(elapsed_ms)
        query_results[q["id"]] = results

        if results and "rerank_score" in results[0]:
            neg_top1_scores.append(results[0]["rerank_score"])
        elif results and "score" in results[0]:
            neg_top1_scores.append(results[0]["score"])
        else:
            neg_top1_scores.append(0.0)

    num_pos = len(positive_queries)
    return {
        "mode": mode_name,
        "hit_at_1": hits_1 / num_pos if num_pos else 0.0,
        "hit_at_5": hits_5 / num_pos if num_pos else 0.0,
        "mrr": statistics.mean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "avg_latency_ms": statistics.mean(latencies) if latencies else 0.0,
        "pos_top1_scores": pos_top1_scores,
        "neg_top1_scores": neg_top1_scores,
        "query_results": query_results,
    }


def main() -> None:
    args = parse_args()
    queries = load_queries(args.config)
    log.info("Loaded %d queries from %s", len(queries), args.config)

    pos_queries = [q for q in queries if q.get("expected") and q["expected"] != "none"]
    neg_queries = [q for q in queries if not q.get("expected") or q["expected"] == "none"]
    log.info("Positive queries: %d, Negative queries: %d", len(pos_queries), len(neg_queries))

    conn = get_connection(autocommit=True)

    modes = [
        "Vector only",
        "FTS only",
        "Hybrid RRF",
    ]
    # Only test reranker if RERANKER_ENABLED
    from retrieval.config import RERANKER_ENABLED

    if RERANKER_ENABLED:
        modes.append("CrossEncoder Rerank")

    all_metrics = []
    detailed_results = {}

    for mode in modes:
        log.info("Evaluating mode: %s...", mode)
        m = evaluate_mode(conn, mode, pos_queries, neg_queries)
        all_metrics.append(m)
        if mode in ("CrossEncoder Rerank", "Hybrid RRF"):
            detailed_results = m["query_results"]

    print("\n" + "=" * 80)
    print("SEARCH EVALUATION REPORT (16 Queries: 13 Positive, 3 Negative)")
    print("=" * 80)
    print(f"{'Mode':<22} | {'Hit@1':<8} | {'Hit@5':<8} | {'MRR':<8} | {'Avg Latency':<12}")
    print("-" * 66)
    for m in all_metrics:
        print(
            f"{m['mode']:<22} | {m['hit_at_1'] * 100:>6.1f}% | {m['hit_at_5'] * 100:>6.1f}% | {m['mrr']:>6.3f}  | {m['avg_latency_ms']:>8.1f} ms"
        )
    print("=" * 80)

    # Reranker score analysis for rejection threshold
    rerank_metrics = next(m for m in all_metrics if m["mode"] == "CrossEncoder Rerank")
    pos_scores = rerank_metrics["pos_top1_scores"]
    neg_scores = rerank_metrics["neg_top1_scores"]

    print("\nREJECTION THRESHOLD ANALYSIS (CrossEncoder Reranker)")
    print("-" * 60)
    if pos_scores:
        print(
            f"Positive queries top-1 scores: min={min(pos_scores):.4f}, median={statistics.median(pos_scores):.4f}, max={max(pos_scores):.4f}"
        )
    if neg_scores:
        print(
            f"Negative queries top-1 scores: min={min(neg_scores):.4f}, median={statistics.median(neg_scores):.4f}, max={max(neg_scores):.4f}"
        )
        threshold_candidate = (min(pos_scores) + max(neg_scores)) / 2.0 if pos_scores else max(neg_scores)
        print(f"Recommended rejection threshold: {threshold_candidate:.4f}")

    print("\n" + "=" * 80)
    print("DETAILED RESULTS FOR SELECTED QUERIES (CrossEncoder Rerank)")
    print("=" * 80)
    q_map = {q["id"]: q for q in queries}
    for qid in args.detail_queries:
        if qid not in q_map:
            continue
        q = q_map[qid]
        res = detailed_results.get(qid, [])
        print(f"\nQuery [{qid}] (lang={q.get('lang')}): \"{q['query']}\"")
        print(f"Expected: {q.get('expected')}")
        if not res:
            print("  (no results returned)")
            continue
        for idx, r in enumerate(res[:3], 1):
            score_val = r.get("rerank_score", r.get("score", 0.0))
            passage = r.get("text", "").replace("\n", " ").strip()[:140]
            print(f"  #{idx} [score={score_val:+.4f}] [{r.get('citation_label', 'N/A')}]")
            if r.get("url"):
                print(f"      URL: {r['url']}")
            print(f"      Text: {passage}...")


if __name__ == "__main__":
    main()
