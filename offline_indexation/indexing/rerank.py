"""Cross-encoder reranking with BAAI/bge-reranker-v2-m3.

Hybrid search candidates -> cross-encoder scores (query, chunk) pairs -> best k.
The score is also the signal for "not in corpus": cosine similarity cannot tell
"about the topic" from "answers the question", a cross-encoder can.
"""

import logging
import time
from functools import cache

from .embeddings import get_device, uses_half_precision

log = logging.getLogger("indexing.rerank")

RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
MAX_LENGTH = 512  # query + passage tokens; enough for our chunks, 2-4x faster than the 8k default
BATCH_SIZE = 16


def get_reranker_model(device: str | None = None):
    return _load_reranker_model(device or get_device())


@cache
def _load_reranker_model(device: str):
    from sentence_transformers import CrossEncoder

    started = time.monotonic()
    model = CrossEncoder(RERANKER_MODEL_NAME, device=device, max_length=MAX_LENGTH)
    if uses_half_precision(device):
        model.model.half()
    log.info("Loaded %s on %s in %.1fs", RERANKER_MODEL_NAME, device, time.monotonic() - started)
    return model


def rerank_candidates(query: str, candidates: list[dict], top_k: int = 5, device: str | None = None) -> list[dict]:
    """Adds `rerank_score` to each candidate and returns the best `top_k`."""
    if not candidates:
        return []
    model = get_reranker_model(device)
    pairs = [(query, c.get("embed_text") or c.get("text", "")) for c in candidates]
    scores = model.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False)
    reranked = [c | {"rerank_score": float(s)} for c, s in zip(candidates, scores, strict=True)]
    reranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    return reranked[:top_k]
