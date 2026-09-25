"""Hybrid search CLI with vector (BAAI/bge-m3) + full-text search (tsvector) and RRF fusion.

Usage:
    uv run python -m indexing.search "cât costă autorizația de construire" -k 5
    uv run python -m indexing.search "как вывезти крупногабаритный мусор" -k 5 --lang ru
"""

import argparse
import hashlib
import logging
import re

import numpy as np
import psycopg
from psycopg.rows import dict_row

from .db import get_connection
from .embeddings import get_device, get_embedding_model

RRF_K = 60

log = logging.getLogger("indexing.search")

STOP_WORDS = {
    # Romanian
    "cat", "cât", "cum", "unde", "ce", "care", "pentru", "este", "sunt",
    "cine", "cand", "când", "daca", "dacă", "acest", "aceasta", "aceste",
    "acesti", "acești",
    # Russian
    "как", "где", "что", "какой", "какая", "какие", "сколько", "для",
    "это", "эта", "этот", "эти", "или", "кто", "когда", "почему",
    "зачем", "было", "быть", "есть", "будет",
}


def clean_tsquery_term(term: str) -> str:
    """Removes tsquery operators and special characters: & | ! ( ) : * ' \\ "."""
    return re.sub(r"[&|!()\\:*\'\"]", "", term).strip()


def build_fts_query(query: str) -> str:
    """Builds a sanitized OR-connected tsquery string for PostgreSQL to_tsquery('simple', ...).

    Discards tokens < 3 characters and RO/RU stop-words.
    Sanitizes special tsquery syntax characters.
    Joins significant tokens with ' | '.
    """
    if not query:
        return ""

    raw_tokens = re.findall(r"[^\s]+", query)
    tokens = []
    for raw in raw_tokens:
        clean = clean_tsquery_term(raw)
        clean = re.sub(r"^[\W_]+|[\W_]+$", "", clean)
        if len(clean) < 3:
            continue
        if clean.lower() in STOP_WORDS:
            continue
        tokens.append(f"'{clean}'")

    if not tokens:
        for raw in raw_tokens:
            clean = clean_tsquery_term(raw)
            clean = re.sub(r"^[\W_]+|[\W_]+$", "", clean)
            if len(clean) >= 2:
                tokens.append(f"'{clean}'")

    return " | ".join(tokens)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m indexing.search", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", help="search query in Romanian or Russian")
    p.add_argument("-k", type=int, default=5, help="number of top results to return (default: 5)")
    p.add_argument("--lang", choices=["ro", "ru", "en", "uk"], help="filter by language")
    p.add_argument("--rerank", action="store_true", help="re-score hybrid candidates with the cross-encoder")
    return p.parse_args()


def kind_priority(item: dict) -> int:
    kind = item.get("kind", "")
    doc_type = item.get("doc_type")
    if kind == "file" and doc_type:
        return 2  # official act file
    if kind == "file":
        return 1  # regular file
    return 0  # page / other


def deduplicate_results(results: list[dict], k: int | None = None) -> list[dict]:
    """Deduplicates search results by content_hash, preferring official act files over pages, then higher RRF score."""
    if not results:
        return []

    groups: dict[str, list[dict]] = {}
    for r in results:
        chash = r.get("content_hash")
        if not chash and r.get("text"):
            chash = hashlib.sha1(r["text"].encode("utf-8")).hexdigest()
        chash = chash or r.get("chunk_id", "")
        groups.setdefault(chash, []).append(r)

    deduped = []
    for items in groups.values():
        best = max(items, key=lambda x: (kind_priority(x), x.get("rrf_score", 0.0)))
        max_rrf = max(x.get("rrf_score", 0.0) for x in items)
        rep = dict(best)
        rep["rrf_score"] = max_rrf
        deduped.append(rep)

    deduped.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)
    return deduped[:k] if k is not None else deduped


# Fields every search mode returns (deduplication and eval rely on them).
RESULT_COLUMNS = ("chunk_id, doc_id, site, kind, doc_type, lang, url, citation_label, "
                  "text, embed_text, content_hash, parent_legal_path")
FTS_QUERY = "(to_tsquery('ro_unaccent', %(q)s) || to_tsquery('ru_unaccent', %(q)s))"


def rrf_fuse(*rankings: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: score = sum of 1 / (RRF_K + rank) over the rankings a chunk is in."""
    fused: dict[str, dict] = {}
    for mode, ranking in zip(("vec_rank", "fts_rank"), rankings, strict=False):
        for rank, item in enumerate(ranking, 1):
            entry = fused.setdefault(item["chunk_id"], item | {"vec_rank": None, "fts_rank": None, "rrf_score": 0.0})
            entry[mode] = rank
            entry["rrf_score"] += 1.0 / (RRF_K + rank)
    for entry in fused.values():
        entry["score"] = entry["rrf_score"]
    return sorted(fused.values(), key=lambda e: e["rrf_score"], reverse=True)


class HybridSearcher:
    """Vector, full-text, hybrid (RRF) and reranked search over the chunks table."""

    def __init__(self, conn: psycopg.Connection | None = None):
        self.conn = conn or get_connection(autocommit=True)
        self.device = get_device()

    @property
    def model(self):
        return get_embedding_model(self.device)

    def _query(self, sql: str, params: dict) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            row["url"] = row["url"] or ""
            row["lang"] = row["lang"] or ""
            row["score"] = row["rrf_score"] = float(row.pop("raw_score"))
        return rows

    def search_vector(self, query: str, k: int = 5, lang: str | None = None, limit: int | None = None) -> list[dict]:
        """bge-m3 cosine similarity."""
        q_vec = np.asarray(self.model.encode([query], normalize_embeddings=True)[0], dtype=np.float32)
        rows = self._query(
            f"""
            SELECT {RESULT_COLUMNS}, 1 - (embedding <=> %(v)s::vector) AS raw_score
            FROM chunks
            WHERE embedding IS NOT NULL AND (%(lang)s::text IS NULL OR lang = %(lang)s::text)
            ORDER BY embedding <=> %(v)s::vector
            LIMIT %(limit)s
            """,
            {"v": q_vec, "lang": lang, "limit": limit or k * 4},
        )
        return deduplicate_results(rows, k=k)

    def search_fts(self, query: str, k: int = 5, lang: str | None = None, limit: int | None = None) -> list[dict]:
        """Full-text search with Romanian and Russian stemming (ro_unaccent | ru_unaccent)."""
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        try:
            rows = self._query(
                f"""
                SELECT {RESULT_COLUMNS}, ts_rank_cd(tsv, {FTS_QUERY}) AS raw_score
                FROM chunks
                WHERE tsv @@ {FTS_QUERY} AND (%(lang)s::text IS NULL OR lang = %(lang)s::text)
                ORDER BY raw_score DESC
                LIMIT %(limit)s
                """,
                {"q": fts_query, "lang": lang, "limit": limit or k * 4},
            )
        except psycopg.Error as e:
            log.warning("FTS query failed for %r (tsquery %r): %s", query, fts_query, e)
            return []
        return deduplicate_results(rows, k=k)

    def search_hybrid(self, query: str, k: int = 5, lang: str | None = None, top_candidates: int = 40) -> list[dict]:
        """Vector + full-text, fused with RRF."""
        vec = self.search_vector(query, k=top_candidates, lang=lang, limit=top_candidates)
        fts = self.search_fts(query, k=top_candidates, lang=lang, limit=top_candidates)
        return deduplicate_results(rrf_fuse(vec, fts), k=k)

    def search_rerank(self, query: str, k: int = 5, lang: str | None = None, top_candidates: int = 30) -> list[dict]:
        """Hybrid top candidates re-scored by the bge-reranker-v2-m3 cross-encoder."""
        from .rerank import rerank_candidates

        candidates = self.search_hybrid(query, k=top_candidates, lang=lang, top_candidates=top_candidates)
        return rerank_candidates(query, candidates, top_k=k, device=self.device)

    def search(self, query: str, k: int = 5, lang: str | None = None, rerank: bool = False) -> list[dict]:
        if rerank:
            return self.search_rerank(query, k=k, lang=lang)
        return self.search_hybrid(query, k=k, lang=lang)


def main() -> None:
    args = parse_args()
    searcher = HybridSearcher()

    results = searcher.search(args.query, k=args.k, lang=args.lang, rerank=args.rerank)
    if not results:
        print(f"No results found for query: '{args.query}'")
        return

    print(f"\nSearch results for: \"{args.query}\"" + (f" [lang={args.lang}]" if args.lang else ""))
    print("=" * 80)
    for i, r in enumerate(results, 1):
        vec_str = f"#{r['vec_rank']}" if r.get("vec_rank") else "-"
        fts_str = f"#{r['fts_rank']}" if r.get("fts_rank") else "-"
        rerank_str = f" | Rerank: {r['rerank_score']:.3f}" if "rerank_score" in r else ""
        print(f"[{i}] RRF: {r['rrf_score']:.4f} | Vec: {vec_str:>3} | FTS: {fts_str:>3}{rerank_str} | Lang: {r['lang']}")
        print(f"    Citation: {r['citation_label']}")
        if r["url"]:
            print(f"    URL:      {r['url']}")
        text_preview = r["text"].replace("\n", " ").strip()[:200]
        print(f"    Passage:  {text_preview}...")
        print("-" * 80)


if __name__ == "__main__":
    main()
