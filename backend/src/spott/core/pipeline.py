"""Unified retrieval pipeline combining chunk vector, line vector, FTS, weighted RRF, and optional reranking."""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import time
from dataclasses import dataclass, field

import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import (
    NOT_FOUND_THRESHOLD,
    RERANK_TOP_K,
    RERANKER_ENABLED,
    RRF_K,
    TOP_CANDIDATES,
    W_FTS,
    W_LINE,
    W_VECTOR,
)
from .embeddings import get_device, get_embedding_model
from .links import make_deep_link
from .rerank import rerank_candidates
from .search import (
    build_fts_query,
    deduplicate_results,
    execute_fts_query,
    execute_line_fts_query,
    execute_line_vector_query,
    execute_vector_query,
    get_chunks_by_ids,
    weighted_rrf_fuse,
)

log = logging.getLogger("retrieval.pipeline")


@dataclass
class RetrievalResult:
    items: list[dict]
    timings_ms: dict[str, float] = field(default_factory=dict)
    not_found: bool = False


@contextlib.contextmanager
def acquire_conn(pool_or_conn: ConnectionPool | psycopg.Connection):
    """Context manager to borrow a connection from a pool or reuse an existing single connection."""
    if isinstance(pool_or_conn, ConnectionPool):
        with pool_or_conn.connection() as conn:
            yield conn
    else:
        yield pool_or_conn


def retrieve(
    pool: ConnectionPool | psycopg.Connection,
    query: str,
    *,
    lang: str | None = None,
    site: str | None = None,
    sites: list[str] | None = None,
    date_after: str | None = None,
    k: int = RERANK_TOP_K,
    rerank: bool = False,
    top_candidates: int = TOP_CANDIDATES,
    w_vector: float | None = None,
    w_fts: float | None = None,
    w_line: float | None = None,
    device: str | None = None,
) -> RetrievalResult:
    """Core retrieval function of the application.

    1. Validates reranker availability (if rerank=True and not RERANKER_ENABLED -> ValueError).
    2. Embeds query with BGE-M3.
    3. Runs vector chunk search, line vector search, and FTS search.
    4. Aggregates matched lines per chunk and fuses rankings with weighted RRF.
    5. Deduplicates candidates.
    6. Reranks with CrossEncoder if rerank=True.
    7. Evaluates rejection threshold for not_found.

    sites / date_after restrict the search to chunks of these sites / with an ISO date after this one.
    """
    if rerank and not RERANKER_ENABLED:
        raise ValueError(
            "Reranker is disabled on this server instance (RERANKER_ENABLED=false). "
            "Set RERANKER_ENABLED=true in .env to enable cross-encoder reranking."
        )

    t_start = time.perf_counter()
    timings: dict[str, float] = {
        "embed": 0.0,
        "vector_sql": 0.0,
        "fts_sql": 0.0,
        "rerank": 0.0,
        "total": 0.0,
    }

    w_vec = W_VECTOR if w_vector is None else w_vector
    w_ft = W_FTS if w_fts is None else w_fts
    w_ln = W_LINE if w_line is None else w_line

    dev = device or get_device()
    model = get_embedding_model(dev)

    # 1. Embed query
    t_embed_start = time.perf_counter()
    q_vec = np.asarray(model.encode([query], normalize_embeddings=True)[0], dtype=np.float32)
    timings["embed"] = round((time.perf_counter() - t_embed_start) * 1000.0, 2)

    fts_q = build_fts_query(query)

    # 2. Parallel or sequential SQL searches
    chunk_vec_results: list[dict] = []
    line_vec_results: list[dict] = []
    fts_results: list[dict] = []
    line_fts_results: list[dict] = []

    def run_chunk_vector() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with acquire_conn(pool) as conn:
            res = execute_vector_query(conn, q_vec, lang=lang, site=site, sites=sites, date_after=date_after, limit=top_candidates)
        return res, (time.perf_counter() - t0) * 1000.0

    def run_line_vector() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with acquire_conn(pool) as conn:
            res = execute_line_vector_query(conn, q_vec, lang=lang, site=site, sites=sites, date_after=date_after, limit=top_candidates * 2)
        return res, (time.perf_counter() - t0) * 1000.0

    def run_fts() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with acquire_conn(pool) as conn:
            res = execute_fts_query(conn, fts_q, lang=lang, site=site, sites=sites, date_after=date_after, limit=top_candidates)
        return res, (time.perf_counter() - t0) * 1000.0

    def run_line_fts() -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        with acquire_conn(pool) as conn:
            res = execute_line_fts_query(conn, fts_q, lang=lang, site=site, sites=sites, date_after=date_after, limit=top_candidates * 2)
        return res, (time.perf_counter() - t0) * 1000.0

    if isinstance(pool, ConnectionPool):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            f_vec = executor.submit(run_chunk_vector)
            f_lvec = executor.submit(run_line_vector)
            f_fts = executor.submit(run_fts)
            f_lfts = executor.submit(run_line_fts)

            chunk_vec_results, vec_ms = f_vec.result()
            line_vec_results, lvec_ms = f_lvec.result()
            fts_results, fts_ms = f_fts.result()
            line_fts_results, lfts_ms = f_lfts.result()
            timings["vector_sql"] = round(max(vec_ms, lvec_ms), 2)
            timings["fts_sql"] = round(max(fts_ms, lfts_ms), 2)
    else:
        chunk_vec_results, vec_ms = run_chunk_vector()
        line_vec_results, lvec_ms = run_line_vector()
        fts_results, fts_ms = run_fts()
        line_fts_results, lfts_ms = run_line_fts()
        timings["vector_sql"] = round(vec_ms + lvec_ms, 2)
        timings["fts_sql"] = round(fts_ms + lfts_ms, 2)

    # 3. Associate matched lines with chunks and build best line ranks
    # Group matched lines by chunk_id
    matched_lines_by_chunk: dict[str, list[dict]] = {}
    for line in line_vec_results + line_fts_results:
        cid = line.get("chunk_id")
        if not cid:
            continue
        line_entry = {
            "line_id": line["line_id"],
            "idx": line["idx"],
            "text": line["text"],
            "score": round(float(line.get("score", 0.0)), 4),
        }
        existing = matched_lines_by_chunk.setdefault(cid, [])
        if not any(e["line_id"] == line["line_id"] for e in existing):
            existing.append(line_entry)

    # Sort lines by score descending within each chunk
    for lines_list in matched_lines_by_chunk.values():
        lines_list.sort(key=lambda x: x["score"], reverse=True)

    # Build chunk ranking from best line vector score
    line_vec_chunks: list[dict] = []
    seen_line_cids: set[str] = set()
    for l in line_vec_results:
        cid = l["chunk_id"]
        if cid not in seen_line_cids:
            seen_line_cids.add(cid)
            line_vec_chunks.append({"chunk_id": cid, "score": l["score"]})

    # Fetch chunk rows for any chunks found only via line search
    known_cids = {c["chunk_id"] for c in chunk_vec_results} | {c["chunk_id"] for c in fts_results}
    missing_cids = list(seen_line_cids - known_cids)
    missing_chunk_map: dict[str, dict] = {}
    if missing_cids:
        with acquire_conn(pool) as conn:
            missing_chunk_map = get_chunks_by_ids(conn, missing_cids)

    # Complete chunk items in line_vec_chunks with full metadata if needed
    for item in line_vec_chunks:
        cid = item["chunk_id"]
        if cid in missing_chunk_map:
            item.update(missing_chunk_map[cid])

    # 4. Weighted RRF Fusion
    rankings_with_weights = [
        (chunk_vec_results, w_vec, "vec_rank"),
        (line_vec_chunks, w_ln, "line_rank"),
        (fts_results, w_ft, "fts_rank"),
    ]
    fused = weighted_rrf_fuse(rankings_with_weights, k=RRF_K)

    # Ensure all fused entries have complete chunk metadata
    for item in fused:
        cid = item["chunk_id"]
        if "text" not in item:
            meta = missing_chunk_map.get(cid)
            if meta:
                item.update(meta)

    # Backfill matched_lines for chunks found only via chunk vector or chunk FTS
    missing_line_cids = [item["chunk_id"] for item in fused if not matched_lines_by_chunk.get(item["chunk_id"])]
    if missing_line_cids:
        with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT line_id, chunk_id, idx, text, page, bboxes
                FROM lines
                WHERE chunk_id = ANY(%s) AND idx < 3
                ORDER BY chunk_id, idx ASC
                """,
                (missing_line_cids,),
            )
            for row in cur.fetchall():
                matched_lines_by_chunk.setdefault(row["chunk_id"], []).append({
                    "line_id": row["line_id"],
                    "idx": row["idx"],
                    "text": row["text"],
                    "page": row.get("page"),
                    "bboxes": row.get("bboxes") or [],
                    "score": 0.0,
                })

    # Attach top 1-3 matched lines to each chunk with full links and metadata
    for item in fused:
        cid = item["chunk_id"]
        c_lines = matched_lines_by_chunk.get(cid, [])[:3]
        for cl in c_lines:
            cl["url"] = item.get("url") or ""
            cl["found_on"] = item.get("found_on")
            cl["citation_label"] = item.get("citation_label") or ""
            if cl.get("page") is None and item.get("pages"):
                cl["page"] = item["pages"][0]
            if not cl.get("bboxes") and item.get("bboxes"):
                cl["bboxes"] = item["bboxes"]
            cl["deep_link"] = make_deep_link(cl["url"], cl["text"], cl.get("page"))
        item["matched_lines"] = c_lines

    # 5. Deduplicate results
    candidates = deduplicate_results(fused, k=top_candidates)

    # 6. Reranking (optional)
    if rerank and candidates:
        t_rerank_start = time.perf_counter()
        candidates = rerank_candidates(
            query=query,
            candidates=candidates,
            top_k=k,
            device=dev,
        )
        timings["rerank"] = round((time.perf_counter() - t_rerank_start) * 1000.0, 2)
    else:
        candidates = candidates[:k]
        timings["rerank"] = 0.0

    timings["total"] = round((time.perf_counter() - t_start) * 1000.0, 2)

    # 7. Check rejection threshold
    not_found = False
    if not candidates:
        not_found = True
    elif rerank:
        top_score = candidates[0].get("rerank_score", 0.0)
        not_found = top_score < NOT_FOUND_THRESHOLD

    return RetrievalResult(
        items=candidates,
        timings_ms=timings,
        not_found=not_found,
    )
