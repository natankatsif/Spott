"""Tests for indexing/search.py: FTS query building, sanitization, stop words, without database."""

import pytest
from indexing.search import build_fts_query, clean_tsquery_term


def test_build_fts_query_romanian_stop_words_and_or():
    q = "cât costă autorizația de construire"
    tsq = build_fts_query(q)
    # 'cât' (stopword) and 'de' (<3 chars) must be excluded
    assert "cât" not in tsq
    assert " 'de' " not in tsq
    # Words joined with ' | '
    assert "'costă'" in tsq
    assert "'autorizația'" in tsq
    assert "'construire'" in tsq
    assert " | " in tsq


def test_build_fts_query_russian_stop_words_and_or():
    q = "как вывезти крупногабаритный мусор"
    tsq = build_fts_query(q)
    # 'как' is a stopword
    assert "как" not in tsq
    assert "'вывезти'" in tsq
    assert "'крупногабаритный'" in tsq
    assert "'мусор'" in tsq
    assert " | " in tsq


def test_build_fts_query_special_characters_sanitized():
    q = "autorizație & (construire) | 2024: *important!*"
    tsq = build_fts_query(q)
    # No raw unquoted special operators outside ' | '
    assert "&" not in tsq
    assert "(" not in tsq
    assert ")" not in tsq
    assert ":" not in tsq
    assert "*" not in tsq
    assert "!" not in tsq
    assert "'autorizație'" in tsq
    assert "'construire'" in tsq
    assert "'2024'" in tsq
    assert "'important'" in tsq


def test_build_fts_query_empty_and_short():
    assert build_fts_query("") == ""
    # Short words fallback
    tsq = build_fts_query("ce e")
    assert "ce" in tsq or tsq == ""
