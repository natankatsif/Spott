"""Embedding model and device selection (BAAI/bge-m3), loaded once per process."""

import logging
import time
from functools import cache

log = logging.getLogger("indexing.embeddings")

MODEL_NAME = "BAAI/bge-m3"
MAX_SEQ_LENGTH = 1024  # chunks are <= ~2800 chars, longer windows only cost time


@cache
def get_device() -> str:
    """'mps', 'cuda' or 'cpu', whichever is available."""
    import torch

    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    log.info("Using device %s", device)
    return device


def uses_half_precision(device: str) -> bool:
    return device in ("mps", "cuda")


def get_embedding_model(device: str | None = None):
    """SentenceTransformer bge-m3, one instance per device; fp16 on GPU/MPS halves memory and time."""
    return _load_embedding_model(device or get_device())


@cache
def _load_embedding_model(device: str):
    from sentence_transformers import SentenceTransformer

    started = time.monotonic()
    model = SentenceTransformer(MODEL_NAME, device=device)
    if uses_half_precision(device):
        model = model.half()
    model.max_seq_length = MAX_SEQ_LENGTH
    log.info("Loaded %s on %s in %.1fs (fp16=%s)", MODEL_NAME, device, time.monotonic() - started,
             uses_half_precision(device))
    return model


def free_device_cache(device: str) -> None:
    """Releases cached GPU/MPS memory between large batches."""
    import torch

    if device == "mps":
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()
