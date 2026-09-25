"""Hybrid search CLI with vector (BAAI/bge-m3) + full-text search (tsvector) and RRF fusion.

Usage:
    uv run python -m indexing.search "cât costă autorizația de construire" -k 5
    uv run python -m indexing.search "как вывезти крупногабаритный мусор" -k 5 --lang ru
"""

import argparse
import logging
import re
import sys
import numpy as np
import psycopg

from .db import get_connection
from .embeddings import get_device, get_embedding_model

MODEL_NAME = "BAAI/bge-m3"
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
    p.add_argument("--lang", choices=["ro", "ru", "en"], help="filter by language")
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

    import hashlib

    groups: dict[str, list[dict]] = {}
    for r in results:
        chash = r.get("content_hash")
        if not chash and r.get("text"):
            chash = hashlib.sha1(r["text"].encode("utf-8")).hexdigest()
        chash = chash or r.get("chunk_id", "")
        groups.setdefault(chash, []).append(r)

    deduped = []
    for chash, items in groups.items():
        best = max(items, key=lambda x: (kind_priority(x), x.get("rrf_score", 0.0)))
        max_rrf = max(x.get("rrf_score", 0.0) for x in items)
        rep = dict(best)
        rep["rrf_score"] = max_rrf
        deduped.append(rep)

    deduped.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)
    return deduped[:k] if k is not None else deduped


class HybridSearcher:
    def __init__(self, conn: psycopg.Connection | None = None):
        self.conn = conn or get_connection(autocommit=True)
        self.device = get_device()
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = get_embedding_model(self.device)
        return self._model

    def search_vector(self, query: str, k: int = 5, lang: str | None = None, limit: int | None = None) -> list[dict]:
        """Pure vector search using BGE-M3 cosine similarity."""
        fetch_limit = limit or (k * 4 if k else 20)
        q_vec = self.model.encode([query], normalize_embeddings=True)[0]
        q_arr = np.array(q_vec, dtype=np.float32)

        results = []
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, citation_label, url, lang, text, embed_text, content_hash, kind, doc_type, doc_id, site,
                       1 - (embedding <=> %s::vector) AS sim
                FROM chunks
                WHERE embedding IS NOT NULL
                  AND (%s::text IS NULL OR lang = %s::text)
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (q_arr, lang, lang, q_arr, fetch_limit),
            )
            for row in cur.fetchall():
                results.append({
                    "chunk_id": row[0],
                    "citation_label": row[1],
                    "url": row[2] or "",
                    "lang": row[3] or "",
                    "text": row[4],
                    "embed_text": row[5],
                    "content_hash": row[6],
                    "kind": row[7],
                    "doc_type": row[8],
                    "doc_id": row[9],
                    "site": row[10],
                    "score": float(row[11]),
                    "rrf_score": float(row[11]),
                })

        return deduplicate_results(results, k=k)

    def search_fts(self, query: str, k: int = 5, lang: str | None = None, limit: int | None = None) -> list[dict]:
        """Pure full-text search with ro_unaccent and ru_unaccent stemming."""
        fetch_limit = limit or (k * 4 if k else 20)
        fts_query = build_fts_query(query)
        if not fts_query:
            return []

        results = []
        with self.conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT chunk_id, citation_label, url, lang, text, embed_text, content_hash, kind, doc_type, doc_id, site,
                           ts_rank_cd(tsv, (to_tsquery('ro_unaccent', %s) || to_tsquery('ru_unaccent', %s))) AS rank_score
                    FROM chunks
                    WHERE tsv @@ (to_tsquery('ro_unaccent', %s) || to_tsquery('ru_unaccent', %s))
                      AND (%s::text IS NULL OR lang = %s::text)
                    ORDER BY rank_score DESC
                    LIMIT %s;
                    """,
                    (fts_query, fts_query, fts_query, fts_query, lang, lang, fetch_limit),
                )
                for row in cur.fetchall():
                    results.append({
                        "chunk_id": row[0],
                        "citation_label": row[1],
                        "url": row[2] or "",
                        "lang": row[3] or "",
                        "text": row[4],
                        "embed_text": row[5],
                        "content_hash": row[6],
                        "kind": row[7],
                        "doc_type": row[8],
                        "doc_id": row[9],
                        "site": row[10],
                        "score": float(row[11]),
                        "rrf_score": float(row[11]),
                    })
            except Exception as e:
                log.warning("FTS query failed for '%s' (fts_query='%s'): %s", query, fts_query, e)

        return deduplicate_results(results, k=k)

    def search_hybrid(self, query: str, k: int = 5, lang: str | None = None, top_candidates: int = 40) -> list[dict]:
        """Hybrid search combining Vector + FTS via Reciprocal Rank Fusion (RRF)."""
        vec_results = self.search_vector(query, k=top_candidates, lang=lang, limit=top_candidates)
        fts_results = self.search_fts(query, k=top_candidates, lang=lang, limit=top_candidates)

        vec_ranks = {c["chunk_id"]: idx + 1 for idx, c in enumerate(vec_results)}
        fts_ranks = {c["chunk_id"]: idx + 1 for idx, c in enumerate(fts_results)}

        chunk_pool = {}
        for c in vec_results:
            chunk_pool[c["chunk_id"]] = c
        for c in fts_results:
            chunk_pool[c["chunk_id"]] = c

        scored = []
        for cid, c in chunk_pool.items():
            vr = vec_ranks.get(cid)
            fr = fts_ranks.get(cid)
            rrf_score = 0.0
            if vr is not None:
                rrf_score += 1.0 / (RRF_K + vr)
            if fr is not None:
                rrf_score += 1.0 / (RRF_K + fr)
            scored.append({
                "chunk_id": cid,
                "rrf_score": rrf_score,
                "score": rrf_score,
                "vec_rank": vr,
                "fts_rank": fr,
                "citation_label": c["citation_label"],
                "url": c["url"],
                "lang": c["lang"],
                "text": c["text"],
                "embed_text": c.get("embed_text") or c["text"],
                "content_hash": c.get("content_hash"),
                "kind": c.get("kind"),
                "doc_type": c.get("doc_type"),
                "doc_id": c.get("doc_id"),
                "site": c.get("site"),
            })

        return deduplicate_results(scored, k=k)

    def search_rerank(self, query: str, k: int = 5, lang: str | None = None, top_candidates: int = 50) -> list[dict]:
        """4th search mode: Hybrid search (top-50) -> BGE-Reranker-v2-m3 -> top-k."""
        from .rerank import rerank_candidates
        candidates = self.search_hybrid(query, k=top_candidates, lang=lang, top_candidates=top_candidates)
        if not candidates:
            return []
        reranked = rerank_candidates(query, candidates, top_k=k, device=self.device)
        return reranked

    def search(self, query: str, k: int = 5, lang: str | None = None) -> list[dict]:
        """Default search method: delegates to search_hybrid."""
        return self.search_hybrid(query, k=k, lang=lang)


def main() -> None:
    args = parse_args()
    searcher = HybridSearcher()

    results = searcher.search(args.query, k=args.k, lang=args.lang)
    if not results:
        print(f"No results found for query: '{args.query}'")
        return

    print(f"\nSearch results for: \"{args.query}\"" + (f" [lang={args.lang}]" if args.lang else ""))
    print("=" * 80)
    for i, r in enumerate(results, 1):
        vec_str = f"#{r['vec_rank']}" if r["vec_rank"] else "-"
        fts_str = f"#{r['fts_rank']}" if r["fts_rank"] else "-"
        print(f"[{i}] RRF: {r['rrf_score']:.4f} | Vec: {vec_str:>3} | FTS: {fts_str:>3} | Lang: {r['lang']}")
        print(f"    Citation: {r['citation_label']}")
        if r["url"]:
            print(f"    URL:      {r['url']}")
        text_preview = r["text"].replace("\n", " ").strip()[:200]
        print(f"    Passage:  {text_preview}...")
        print("-" * 80)


if __name__ == "__main__":
    main()
