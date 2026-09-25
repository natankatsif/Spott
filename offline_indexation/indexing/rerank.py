"""CrossEncoder reranking using BAAI/bge-reranker-v2-m3.

Used as 4th search mode in smoke-eval:
Hybrid search (top-50) -> CrossEncoder reranker -> top-k.
"""

import logging
from typing import Any
from .embeddings import get_device

log = logging.getLogger("indexing.rerank")

RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_reranker_model: Any = None


def get_reranker_model(device: str | None = None):
    """Lazily loads and returns the CrossEncoder reranker model."""
    global _reranker_model
    if _reranker_model is None:
        target_device = device or get_device()
        log.info("Loading reranker model '%s' on %s...", RERANKER_MODEL_NAME, target_device)
        from sentence_transformers import CrossEncoder
        _reranker_model = CrossEncoder(RERANKER_MODEL_NAME, device=target_device)
    return _reranker_model


def rerank_candidates(query: str, candidates: list[dict], top_k: int = 5, device: str | None = None) -> list[dict]:
    """Reranks candidate chunks with CrossEncoder, assigns rerank_score, and returns top_k."""
    if not candidates:
        return []

    model = get_reranker_model(device=device)
    pairs = [[query, c.get("embed_text") or c.get("text", "")] for c in candidates]
    scores = model.predict(pairs, batch_size=32)

    reranked = []
    for c, score in zip(candidates, scores):
        item = dict(c)
        item["rerank_score"] = float(score)
        reranked.append(item)

    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    return reranked[:top_k]
