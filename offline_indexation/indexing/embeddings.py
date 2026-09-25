"""Embedding model management and device selection for BAAI/bge-m3."""

import logging
import time
from typing import Optional

log = logging.getLogger("indexing.embeddings")
MODEL_NAME = "BAAI/bge-m3"

_cached_device: Optional[str] = None
_cached_model = None


def get_device() -> str:
    """Returns 'mps', 'cuda', or 'cpu' depending on hardware availability."""
    global _cached_device
    if _cached_device is None:
        import torch
        if torch.backends.mps.is_available():
            _cached_device = "mps"
        elif torch.cuda.is_available():
            _cached_device = "cuda"
        else:
            _cached_device = "cpu"
        log.info("Using device %s for embedding model %s", _cached_device, MODEL_NAME)
    return _cached_device


def get_embedding_model(device: str | None = None):
    """Lazily loads and caches the SentenceTransformer model singleton."""
    global _cached_model
    if _cached_model is None:
        from sentence_transformers import SentenceTransformer
        dev = device or get_device()
        t = time.monotonic()
        log.info("Loading %s on %s...", MODEL_NAME, dev)
        _cached_model = SentenceTransformer(MODEL_NAME, device=dev)
        log.info("Model loaded in %.1fs", time.monotonic() - t)
    return _cached_model
