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
import torch
from sentence_transformers import SentenceTransformer

from .db import get_connection

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


class HybridSearcher:
    def __init__(self, conn: psycopg.Connection | None = None):
        self.conn = conn or get_connection(autocommit=True)
        if torch.backends.mps.is_available():
            self.device = "mps"
        elif torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(MODEL_NAME, device=self.device)
        return self._model

    def search(self, query: str, k: int = 5, lang: str | None = None) -> list[dict]:
        # 1. Vector search (top-40)
        q_vec = self.model.encode([query], normalize_embeddings=True)[0]
        q_arr = np.array(q_vec, dtype=np.float32)

        vec_results = []
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, citation_label, url, lang, text,
                       1 - (embedding <=> %s::vector) AS sim
                FROM chunks
                WHERE embedding IS NOT NULL
                  AND (%s IS NULL OR lang = %s)
                ORDER BY embedding <=> %s::vector
                LIMIT 40;
                """,
                (q_arr, lang, lang, q_arr),
            )
            for row in cur.fetchall():
                vec_results.append({
                    "chunk_id": row[0],
                    "citation_label": row[1],
                    "url": row[2] or "",
                    "lang": row[3] or "",
                    "text": row[4],
                    "score": row[5],
                })

        # 2. Full-text search (top-40)
        fts_results = []
        fts_query = build_fts_query(query)
        if fts_query:
            with self.conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT chunk_id, citation_label, url, lang, text,
                               ts_rank_cd(tsv, to_tsquery('simple', immutable_unaccent(%s))) AS rank_score
                        FROM chunks
                        WHERE tsv @@ to_tsquery('simple', immutable_unaccent(%s))
                          AND (%s IS NULL OR lang = %s)
                        ORDER BY rank_score DESC
                        LIMIT 40;
                        """,
                        (fts_query, fts_query, lang, lang),
                    )
                    for row in cur.fetchall():
                        fts_results.append({
                            "chunk_id": row[0],
                            "citation_label": row[1],
                            "url": row[2] or "",
                            "lang": row[3] or "",
                            "text": row[4],
                            "score": row[5],
                        })
                except Exception as e:
                    log.warning("FTS query failed for '%s' (fts_query='%s'): %s", query, fts_query, e)


        # 3. RRF fusion (k=60)
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
                "vec_rank": vr,
                "fts_rank": fr,
                "citation_label": c["citation_label"],
                "url": c["url"],
                "lang": c["lang"],
                "text": c["text"],
            })

        scored.sort(key=lambda x: x["rrf_score"], reverse=True)
        return scored[:k]


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
