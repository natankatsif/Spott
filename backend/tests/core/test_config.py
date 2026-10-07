"""Tests for retrieval configuration constants."""

from spott.core.config import EMBEDDING_MODEL_NAME, TOP_CANDIDATES, TOP_K


def test_config_constants():
    assert TOP_CANDIDATES == 30
    assert TOP_K == 8
    assert "bge-m3" in EMBEDDING_MODEL_NAME
