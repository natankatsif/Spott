"""Cross-encoder reranking with BAAI/bge-reranker-v2-m3."""

import logging
import time
from functools import cache

from .config import RERANKER_BATCH_SIZE, RERANKER_MAX_LENGTH, RERANKER_MODEL_NAME
from .embeddings import get_device, uses_half_precision

log = logging.getLogger("retrieval.rerank")


def get_reranker_model(device: str | None = None):
    return _load_reranker_model(device or get_device())


@cache
def _load_reranker_model(device: str):
    from sentence_transformers import CrossEncoder

    started = time.monotonic()
    model = CrossEncoder(RERANKER_MODEL_NAME, device=device, max_length=RERANKER_MAX_LENGTH)
    if uses_half_precision(device):
        model.model.half()
    log.info("Loaded %s on %s in %.1fs", RERANKER_MODEL_NAME, device, time.monotonic() - started)
    return model


def rerank_candidates(
    query: str,
    candidates: list[dict],
    top_k: int = 5,
    device: str | None = None,
    batch_size: int = RERANKER_BATCH_SIZE,
) -> list[dict]:
    """Adds `rerank_score` to each candidate and returns the best `top_k`."""
    if not candidates:
        return []
    model = get_reranker_model(device)
    pairs = [(query, c.get("embed_text") or c.get("text", "")) for c in candidates]
    scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    reranked = [c | {"rerank_score": float(s)} for c, s in zip(candidates, scores, strict=True)]
    reranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    return reranked[:top_k]
