"""Latency benchmark for retrieval pipeline and tools (search, grep, toc, open).

Benchmarks:
  1. search (POST /api/tools/search or /api/search): p95 < 300 ms
  2. grep   (POST /api/tools/grep):                  p95 < 50 ms
  3. toc    (POST /api/tools/toc):                   p95 < 50 ms
  4. open   (POST /api/tools/open):                  p95 < 50 ms
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import yaml

GREP_PATTERNS = [
    "Chișinău",
    "autorizație",
    "teren",
    "regulament",
    "primar",
    "buget",
    "servicii",
    "deșeuri",
    "transport",
    "imobil",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Latency benchmark for tools and retrieval")
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


def bench_search(client: httpx.Client, url: str, queries: list[dict], runs: int) -> dict[str, list[float]]:
    search_url = f"{url.rstrip('/')}/api/tools/search"
    timings: dict[str, list[float]] = {
        "embed": [],
        "vector_sql": [],
        "fts_sql": [],
        "server_total": [],
        "client_roundtrip": [],
    }

    print(f"\n1. Benchmarking search ({len(queries)} queries x {runs} runs = {len(queries) * runs} reqs)...")
    for r in range(1, runs + 1):
        for q in queries:
            t0 = time.perf_counter()
            resp = client.post(
                search_url,
                json={"query": q["query"], "lang": q.get("lang"), "k": 8},
                timeout=60.0,
            )
            roundtrip_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                t = resp.json().get("timings_ms", {})
                timings["embed"].append(t.get("embed", 0.0))
                timings["vector_sql"].append(t.get("vector_sql", 0.0))
                timings["fts_sql"].append(t.get("fts_sql", 0.0))
                timings["server_total"].append(t.get("total", 0.0))
                timings["client_roundtrip"].append(roundtrip_ms)
        print(f"   Run {r}/{runs} finished.")
    return timings


def bench_grep(client: httpx.Client, url: str, runs: int) -> list[float]:
    grep_url = f"{url.rstrip('/')}/api/tools/grep"
    latencies: list[float] = []

    print(f"\n2. Benchmarking grep ({len(GREP_PATTERNS)} patterns x {runs} runs = {len(GREP_PATTERNS) * runs} reqs)...")
    for r in range(1, runs + 1):
        for pat in GREP_PATTERNS:
            t0 = time.perf_counter()
            resp = client.post(grep_url, json={"pattern": pat, "limit": 20}, timeout=10.0)
            ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                latencies.append(ms)
        print(f"   Run {r}/{runs} finished.")
    return latencies


def get_sample_doc_ids(client: httpx.Client, url: str) -> list[str]:
    resp = client.post(
        f"{url.rstrip('/')}/api/tools/search",
        json={"query": "Chișinău primăria decizie", "k": 5},
        timeout=30.0,
    )
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        return list({item["doc_id"] for item in items if item.get("doc_id")})
    return []


def bench_toc(client: httpx.Client, url: str, doc_ids: list[str], runs: int) -> list[float]:
    toc_url = f"{url.rstrip('/')}/api/tools/toc"
    latencies: list[float] = []

    print(f"\n3. Benchmarking toc ({len(doc_ids)} docs x {runs} runs = {len(doc_ids) * runs} reqs)...")
    for r in range(1, runs + 1):
        for doc_id in doc_ids:
            t0 = time.perf_counter()
            resp = client.post(toc_url, json={"doc_id": doc_id}, timeout=10.0)
            ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                latencies.append(ms)
        print(f"   Run {r}/{runs} finished.")
    return latencies


def bench_open(client: httpx.Client, url: str, doc_ids: list[str], runs: int) -> list[float]:
    open_url = f"{url.rstrip('/')}/api/tools/open"
    latencies: list[float] = []

    print(f"\n4. Benchmarking open ({len(doc_ids)} docs x {runs} runs = {len(doc_ids) * runs} reqs)...")
    for r in range(1, runs + 1):
        for doc_id in doc_ids:
            t0 = time.perf_counter()
            resp = client.post(open_url, json={"doc_id": doc_id, "max_lines": 60}, timeout=10.0)
            ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                latencies.append(ms)
        print(f"   Run {r}/{runs} finished.")
    return latencies


def main() -> None:
    args = parse_args()
    wait_for_server(args.url)

    with open(args.config, encoding="utf-8") as f:
        queries = yaml.safe_load(f)

    client = httpx.Client(timeout=60.0)

    # 1. Search benchmark
    search_timings = bench_search(client, args.url, queries, args.runs)

    # 2. Grep benchmark
    grep_latencies = bench_grep(client, args.url, args.runs)

    # 3. Sample doc_ids for toc and open
    doc_ids = get_sample_doc_ids(client, args.url)
    if not doc_ids:
        doc_ids = ["file:0b5154e4c7bd9f048be29b6216c670e9f95b483fd76f6d25bca7b9b828a78d84"]

    # 4. Toc benchmark
    toc_latencies = bench_toc(client, args.url, doc_ids, args.runs)

    # 5. Open benchmark
    open_latencies = bench_open(client, args.url, doc_ids, args.runs)

    print("\n" + "=" * 70)
    print("LATENCY BENCHMARK RESULTS (Tools & Retrieval)")
    print("=" * 70)
    print(f"{'Tool / Operation':<25} | {'p50 (median)':<16} | {'p95':<16} | Target")
    print("-" * 70)

    # Search breakdown
    s_tot = search_timings.get("server_total", [])
    if s_tot:
        p50 = float(np.percentile(s_tot, 50))
        p95 = float(np.percentile(s_tot, 95))
        status = "PASS" if p95 < 300.0 else "FAIL"
        print(f"{'search (total)':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   | < 300 ms [{status}]")
    if search_timings.get("embed"):
        p50 = float(np.percentile(search_timings["embed"], 50))
        p95 = float(np.percentile(search_timings["embed"], 95))
        print(f"{'  ↳ embed':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   |")
    if search_timings.get("vector_sql"):
        p50 = float(np.percentile(search_timings["vector_sql"], 50))
        p95 = float(np.percentile(search_timings["vector_sql"], 95))
        print(f"{'  ↳ vector_sql':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   |")
    if search_timings.get("fts_sql"):
        p50 = float(np.percentile(search_timings["fts_sql"], 50))
        p95 = float(np.percentile(search_timings["fts_sql"], 95))
        print(f"{'  ↳ fts_sql':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   |")

    # Grep
    if grep_latencies:
        p50 = float(np.percentile(grep_latencies, 50))
        p95 = float(np.percentile(grep_latencies, 95))
        status = "PASS" if p95 < 50.0 else "FAIL"
        print(f"{'grep':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   | < 50 ms  [{status}]")

    # Toc
    if toc_latencies:
        p50 = float(np.percentile(toc_latencies, 50))
        p95 = float(np.percentile(toc_latencies, 95))
        status = "PASS" if p95 < 50.0 else "FAIL"
        print(f"{'toc':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   | < 50 ms  [{status}]")

    # Open
    if open_latencies:
        p50 = float(np.percentile(open_latencies, 50))
        p95 = float(np.percentile(open_latencies, 95))
        status = "PASS" if p95 < 50.0 else "FAIL"
        print(f"{'open':<25} | {p50:>11.2f} ms   | {p95:>11.2f} ms   | < 50 ms  [{status}]")

    print("=" * 70)


if __name__ == "__main__":
    main()
