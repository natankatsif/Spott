"""Embedding model loading and hardware acceleration helpers."""

import logging
import time
from functools import cache

import numpy as np

from .config import EMBEDDING_MODEL_NAME

log = logging.getLogger("retrieval.embeddings")


def get_device() -> str:
    """Selects the best available accelerator: MPS on Mac, CUDA on Linux/Windows, CPU fallback."""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def uses_half_precision(device: str) -> bool:
    return device in ("mps", "cuda")


@cache
def _load_model(device: str):
    from sentence_transformers import SentenceTransformer

    started = time.monotonic()
    half = uses_half_precision(device)
    model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=device)
    if half:
        model.half()
    log.info("Loaded %s on %s in %.1fs (fp16=%s)", EMBEDDING_MODEL_NAME, device, time.monotonic() - started, half)
    return model


def get_embedding_model(device: str | None = None):
    """Returns the cached SentenceTransformer instance for the given device."""
    return _load_model(device or get_device())


def embed_texts(texts: list[str]) -> np.ndarray:
    """Normalized embeddings of the texts, one float32 row each (cosine similarity = dot product)."""
    return np.asarray(get_embedding_model().encode(texts, normalize_embeddings=True), dtype=np.float32)


def free_device_cache(device: str) -> None:
    """Frees accelerator memory between large batches to avoid swap."""
    import torch

    if device == "mps" and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()
