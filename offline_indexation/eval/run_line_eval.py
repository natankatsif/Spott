"""Runs comprehensive line and chunk search evaluation on eval/lines.yaml.

Metrics:
  - line hit@1, line hit@5
  - chunk hit@5
  - line MRR, chunk MRR
  - Avg Latency (ms)
  - Negative score distribution (p50, max score)

Evaluates:
  1. Chunk-vector only (w_vec=1.0, w_line=0.0, w_fts=0.0)
  2. Line-vector only (w_vec=0.0, w_line=1.0, w_fts=0.0)
  3. Lines + Chunks vector (w_vec=1.0, w_line=1.0, w_fts=0.0)
  4. Hybrid RRF: (w_vec=1.0, w_line=1.0, w_fts=0.2)
  5. Hybrid RRF: (w_vec=1.0, w_line=1.0, w_fts=0.5) [current default]
  6. Hybrid RRF: (w_vec=1.0, w_line=1.0, w_fts=1.0)
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path
from typing import Any

import yaml
from retrieval.db import get_connection
from retrieval.pipeline import retrieve


def load_dataset(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def evaluate_mode(
    conn: Any,
    queries: list[dict[str, Any]],
    *,
    mode_name: str,
    w_vector: float,
    w_line: float,
    w_fts: float,
    k: int = 10,
) -> dict[str, Any]:
    positives = [q for q in queries if not q.get("is_negative")]
    negatives = [q for q in queries if q.get("is_negative")]

    line_hits_1 = 0
    line_hits_5 = 0
    chunk_hits_5 = 0
    line_rr_list: list[float] = []
    chunk_rr_list: list[float] = []
    latencies: list[float] = []

    for q in positives:
        t0 = time.perf_counter()
        res = retrieve(
            conn,
            q["query"],
            k=k,
            w_vector=w_vector,
            w_line=w_line,
            w_fts=w_fts,
            rerank=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

        gold_line_id = q["gold_line_id"]
        gold_chunk_id = q["gold_chunk_id"]

        # 1. Chunk hit & MRR
        chunk_rank = 0
        for rank, item in enumerate(res.items, 1):
            if item.get("chunk_id") == gold_chunk_id:
                chunk_rank = rank
                break
        if 1 <= chunk_rank <= 5:
            chunk_hits_5 += 1
        chunk_rr_list.append(1.0 / chunk_rank if chunk_rank > 0 else 0.0)

        # 2. Flatten matched lines in rank order
        ordered_lines: list[str] = []
        for item in res.items:
            for l in item.get("matched_lines", []):
                lid = l.get("line_id")
                if lid and lid not in ordered_lines:
                    ordered_lines.append(lid)

        line_rank = 0
        for rank, lid in enumerate(ordered_lines, 1):
            if lid == gold_line_id:
                line_rank = rank
                break

        if line_rank == 1:
            line_hits_1 += 1
        if 1 <= line_rank <= 5:
            line_hits_5 += 1
        line_rr_list.append(1.0 / line_rank if line_rank > 0 else 0.0)

    # Negatives top score distribution
    neg_top_scores: list[float] = []
    for q in negatives:
        res = retrieve(
            conn,
            q["query"],
            k=5,
            w_vector=w_vector,
            w_line=w_line,
            w_fts=w_fts,
            rerank=False,
        )
        if res.items:
            neg_top_scores.append(float(res.items[0].get("score", 0.0)))
        else:
            neg_top_scores.append(0.0)

    n_pos = len(positives)
    return {
        "mode": mode_name,
        "w_vec": w_vector,
        "w_line": w_line,
        "w_fts": w_fts,
        "line_hit@1": round(line_hits_1 / n_pos, 4),
        "line_hit@5": round(line_hits_5 / n_pos, 4),
        "chunk_hit@5": round(chunk_hits_5 / n_pos, 4),
        "line_mrr": round(statistics.mean(line_rr_list) if line_rr_list else 0.0, 4),
        "chunk_mrr": round(statistics.mean(chunk_rr_list) if chunk_rr_list else 0.0, 4),
        "avg_ms": round(statistics.mean(latencies), 1),
        "neg_score_p50": round(statistics.median(neg_top_scores) if neg_top_scores else 0.0, 5),
        "neg_score_max": round(max(neg_top_scores) if neg_top_scores else 0.0, 5),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate line and chunk search quality")
    p.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).parent / "lines.yaml",
        help="Path to lines.yaml",
    )
    args = p.parse_args()

    queries = load_dataset(args.dataset)
    positives = [q for q in queries if not q.get("is_negative")]
    negatives = [q for q in queries if q.get("is_negative")]
    print(f"Loaded {len(queries)} queries ({len(positives)} positive, {len(negatives)} negative) from {args.dataset}")

    configurations = [
        ("1. Chunks-vector only", 1.0, 0.0, 0.0),
        ("2. Lines-vector only", 0.0, 1.0, 0.0),
        ("3. Lines + Chunks vector", 1.0, 1.0, 0.0),
        ("4. Hybrid (w_fts=0.2)", 1.0, 1.0, 0.2),
        ("5. Hybrid (w_fts=0.5)", 1.0, 1.0, 0.5),
        ("6. Hybrid (w_fts=1.0)", 1.0, 1.0, 1.0),
    ]

    conn = get_connection()
    results = []
    try:
        for name, w_v, w_l, w_f in configurations:
            print(f"Evaluating {name}...")
            r = evaluate_mode(conn, queries, mode_name=name, w_vector=w_v, w_line=w_l, w_fts=w_f)
            results.append(r)
    finally:
        conn.close()

    print("\n" + "=" * 105)
    print("LINE & CHUNK SEARCH EVALUATION RESULTS:")
    print("=" * 105)
    header = f"{'Mode':<26} | {'Line Hit@1':<10} | {'Line Hit@5':<10} | {'Chunk Hit@5':<11} | {'Line MRR':<9} | {'Chunk MRR':<9} | {'Avg ms':<7} | {'Neg Max'}"
    print(header)
    print("-" * 105)
    for r in results:
        print(
            f"{r['mode']:<26} | "
            f"{r['line_hit@1']:<10.2%} | "
            f"{r['line_hit@5']:<10.2%} | "
            f"{r['chunk_hit@5']:<11.2%} | "
            f"{r['line_mrr']:<9.4f} | "
            f"{r['chunk_mrr']:<9.4f} | "
            f"{r['avg_ms']:<7.1f} | "
            f"{r['neg_score_max']:.5f}"
        )
    print("=" * 105)


if __name__ == "__main__":
    main()
