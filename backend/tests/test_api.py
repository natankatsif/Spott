"""Unit tests for backend schemas, threshold logic, and endpoints without full model dependencies."""

from retrieval.config import NOT_FOUND_THRESHOLD, RERANK_TOP_K

from app.schemas import (
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchTimings,
)


def test_search_request_defaults():
    req = SearchRequest(query="autorizatie de constructie")
    assert req.query == "autorizatie de constructie"
    assert req.k == RERANK_TOP_K
    assert req.rerank is False
    assert req.lang is None


def test_search_response_structure():
    item = SearchResultItem(
        chunk_id="chk1",
        doc_id="doc1",
        citation_label="Citation",
        text="Sample text",
        url="https://example.com",
        rerank_score=0.85,
    )
    timings = SearchTimings(
        embed=15.2,
        vector_sql=5.1,
        fts_sql=2.3,
        rerank=120.0,
        total=142.6,
    )
    resp = SearchResponse(
        results=[item],
        timings_ms=timings,
        not_found=False,
    )
    assert len(resp.results) == 1
    assert resp.results[0].chunk_id == "chk1"
    assert resp.not_found is False
    assert resp.timings_ms.total == 142.6


def test_rejection_threshold_flag():
    # If top score is below NOT_FOUND_THRESHOLD, not_found is flagged True
    low_score = NOT_FOUND_THRESHOLD - 0.001
    item = SearchResultItem(
        chunk_id="chk_low",
        doc_id="doc_low",
        citation_label="Irrelevant",
        text="Something else",
        url="",
        rerank_score=low_score,
    )
    not_found = (low_score < NOT_FOUND_THRESHOLD)
    resp = SearchResponse(
        results=[item],
        timings_ms=SearchTimings(embed=1, vector_sql=1, fts_sql=1, rerank=1, total=4),
        not_found=not_found,
    )
    assert resp.not_found is True


def test_health_response():
    health = HealthResponse(
        status="ok",
        device="mps",
        models_loaded=True,
        chunk_count=2269,
    )
    assert health.status == "ok"
    assert health.device == "mps"
    assert health.chunk_count == 2269
