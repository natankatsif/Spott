"""Embedding model loading and hardware acceleration helpers (re-exported from retrieval)."""

from retrieval.embeddings import (
    free_device_cache,
    get_device,
    get_embedding_model,
    uses_half_precision,
)

__all__ = [
    "free_device_cache",
    "get_device",
    "get_embedding_model",
    "uses_half_precision",
]
