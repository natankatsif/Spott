"""Tests for retrieval configuration constants."""

from spott.core.config import (
    EMBEDDING_MODEL_NAME,
    NOT_FOUND_THRESHOLD,
    RERANK_TOP_K,
    RERANKER_MODEL_NAME,
    TOP_CANDIDATES,
)


def test_config_constants():
    assert TOP_CANDIDATES == 30
    assert RERANK_TOP_K == 8
    assert 0.005 < NOT_FOUND_THRESHOLD < 0.015
    assert "bge-m3" in EMBEDDING_MODEL_NAME
    assert "bge-reranker" in RERANKER_MODEL_NAME
