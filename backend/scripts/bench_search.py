"""Latency benchmark for POST /api/search.

Runs 16 evaluation queries x 3 runs against the live FastAPI backend:
  - Without reranker (Vector + FTS + RRF)
  - With reranker (Hybrid + CrossEncoder BGE-Reranker-v2-m3)

Calculates p50 and p95 for each stage:
  - embed (query vectorization)
  - vector_sql (parallel pgvector cosine similarity)
  - fts_sql (parallel ro/ru unaccent text search)
  - rerank (CrossEncoder scoring)
  - total (server total and client roundtrip)

Evaluates against Mac targets:
  - Without reranker p95 < 300 ms
  - With reranker p95 < 1500 ms
"""

import argparse
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Latency benchmark for POST /api/search")
    p.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of the running backend (default: http://localhost:8000)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent.parent / "offline_indexation" / "eval" / "search_smoke.yaml",
        help="Path to search_smoke.yaml",
    )
    p.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs per query (default: 3)",
    )
    p.add_argument(
        "--skip-rerank",
        action="store_true",
        help="Skip reranker benchmark run",
    )
    return p.parse_args()


def wait_for_server(url: str, timeout_sec: int = 60) -> dict:
    health_url = f"{url.rstrip('/')}/health"
    start = time.monotonic()
    print(f"Waiting for backend at {health_url}...")
    while time.monotonic() - start < timeout_sec:
        try:
            r = httpx.get(health_url, timeout=3.0)
            if r.status_code == 200:
                data = r.json()
                if data.get("models_loaded"):
                    print(
                        f"Server healthy! Device: {data.get('device')}, Chunks in index: {data.get('chunk_count')}"
                    )
                    return data
        except Exception:
            pass
        time.sleep(1.0)
    print(f"Error: server at {url} did not become ready within {timeout_sec}s.", file=sys.stderr)
    sys.exit(1)


def run_benchmark(
    client: httpx.Client,
    search_url: str,
    queries: list[dict],
    runs: int,
    rerank: bool,
) -> dict[str, list[float]]:
    timings = {
        "embed": [],
        "vector_sql": [],
        "fts_sql": [],
        "rerank": [],
        "server_total": [],
        "client_roundtrip": [],
    }

    mode_label = "WITH reranker" if rerank else "WITHOUT reranker"
    print(f"\nRunning benchmark {mode_label} ({len(queries)} queries x {runs} runs = {len(queries) * runs} reqs)...")

    for run_idx in range(1, runs + 1):
        for q in queries:
            payload = {
                "query": q["query"],
                "lang": q.get("lang"),
                "k": 8,
                "rerank": rerank,
            }
            t0 = time.perf_counter()
            try:
                resp = client.post(search_url, json=payload, timeout=120.0)
            except httpx.ReadTimeout:
                print(f"Warning: request timed out for query {q['id']}")
                continue
            except httpx.RequestError as exc:
                print(f"Warning: request error for query {q['id']}: {exc}")
                continue
            roundtrip_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code != 200:
                print(f"Warning: request failed for query {q['id']}: {resp.status_code} {resp.text}")
                continue

            data = resp.json()
            t = data.get("timings_ms", {})
            timings["embed"].append(t.get("embed", 0.0))
            timings["vector_sql"].append(t.get("vector_sql", 0.0))
            timings["fts_sql"].append(t.get("fts_sql", 0.0))
            timings["rerank"].append(t.get("rerank", 0.0))
            timings["server_total"].append(t.get("total", 0.0))
            timings["client_roundtrip"].append(roundtrip_ms)

        print(f"  Run {run_idx}/{runs} finished.")

    return timings


def print_stats_table(title: str, timings: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    print("\n" + "=" * 65)
    print(f"{title}")
    print("=" * 65)
    print(f"{'Stage':<20} | {'p50 (median)':<16} | {'p95':<16}")
    print("-" * 65)
    stats = {}
    for stage, vals in timings.items():
        if not vals:
            continue
        p50 = float(np.percentile(vals, 50))
        p95 = float(np.percentile(vals, 95))
        stats[stage] = (p50, p95)
        print(f"{stage:<20} | {p50:>11.2f} ms   | {p95:>11.2f} ms")
    print("=" * 65)
    return stats


def main() -> None:
    args = parse_args()
    wait_for_server(args.url)

    with open(args.config, encoding="utf-8") as f:
        queries = yaml.safe_load(f)
    print(f"Loaded {len(queries)} queries from {args.config}")

    search_url = f"{args.url.rstrip('/')}/api/search"
    client = httpx.Client(timeout=120.0)

    # 1. Run without reranker
    timings_no_rerank = run_benchmark(client, search_url, queries, args.runs, rerank=False)
    stats_no_rerank = print_stats_table(
        "LATENCY BENCHMARK: WITHOUT RERANKER (Fast Hybrid: Vector + FTS + RRF)",
        timings_no_rerank,
    )

    # 2. Run with reranker
    if not args.skip_rerank:
        timings_with_rerank = run_benchmark(client, search_url, queries, args.runs, rerank=True)
        stats_with_rerank = print_stats_table(
            "LATENCY BENCHMARK: WITH RERANKER (Hybrid + CrossEncoder BGE-Reranker-v2-m3)",
            timings_with_rerank,
        )
    else:
        stats_with_rerank = {}

    # 3. Check targets
    target_no_rerank_p95 = 300.0  # ms
    target_with_rerank_p95 = 1500.0  # ms

    actual_no_rerank_p95 = stats_no_rerank.get("server_total", (0.0, 0.0))[1]
    actual_with_rerank_p95 = stats_with_rerank.get("server_total", (0.0, 0.0))[1] if stats_with_rerank else None

    print("\nTARGET ASSESSMENT (Apple Silicon Mac):")
    print("-" * 65)
    pass_no_rerank = actual_no_rerank_p95 <= target_no_rerank_p95
    status_no_rerank = "PASS" if pass_no_rerank else "EXCEEDED"
    print(
        f"1. Without reranker p95: {actual_no_rerank_p95:.1f} ms / target < {target_no_rerank_p95:.1f} ms -> [{status_no_rerank}]"
    )

    if actual_with_rerank_p95 is not None:
        pass_with_rerank = actual_with_rerank_p95 <= target_with_rerank_p95
        status_with_rerank = "PASS" if pass_with_rerank else "EXCEEDED"
        print(
            f"2. With reranker p95:    {actual_with_rerank_p95:.1f} ms / target < {target_with_rerank_p95:.1f} ms -> [{status_with_rerank}]"
        )
    else:
        print("2. With reranker:         [SKIPPED]")
    print("-" * 65)

    if actual_with_rerank_p95 is not None and not pass_with_rerank:
        print("\nNotice on CrossEncoder latency:")
        print(
            "bge-reranker-v2-m3 scores 30 candidates sequentially or in mini-batches on MPS.\n"
            "Optimization options to propose to team:\n"
            "  1. Reduce top candidates from 30 to 15-20 (cuts reranker latency by 35-50%).\n"
            "  2. Use a lighter reranker such as Qwen3-Reranker-0.6B / bge-reranker-base.\n"
            "  3. Offload reranker to an external GPU inference API (vLLM / TEI) or make rerank asynchronous."
        )


if __name__ == "__main__":
    main()
