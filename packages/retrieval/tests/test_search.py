"""Pure unit tests for search: FTS query building, deduplication, and RRF fusion without DB or models."""

from retrieval.search import build_fts_query, deduplicate_results, rrf_fuse


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
    tsq = build_fts_query("ce e")
    assert "ce" in tsq or tsq == ""


def test_deduplicate_results_prefers_act_over_page():
    items = [
        {
            "chunk_id": "c_page",
            "content_hash": "hash_same",
            "kind": "page",
            "doc_type": None,
            "rrf_score": 0.035,
            "text": "Text comun",
            "citation_label": "Pagina oficiala",
        },
        {
            "chunk_id": "c_act",
            "content_hash": "hash_same",
            "kind": "file",
            "doc_type": "decizie",
            "rrf_score": 0.025,
            "text": "Text comun",
            "citation_label": "Decizia nr. 10/2",
        },
    ]

    deduped = deduplicate_results(items, k=5)
    assert len(deduped) == 1
    # File-act must win over page even if page had higher initial score
    assert deduped[0]["chunk_id"] == "c_act"
    assert deduped[0]["kind"] == "file"
    assert deduped[0]["doc_type"] == "decizie"
    # Inherits top RRF score
    assert deduped[0]["rrf_score"] == 0.035


def test_deduplicate_results_prefers_higher_rrf_for_same_kind():
    items = [
        {
            "chunk_id": "p1",
            "content_hash": "hash_rep",
            "kind": "page",
            "doc_type": None,
            "rrf_score": 0.010,
            "text": "Informatii utile",
        },
        {
            "chunk_id": "p2",
            "content_hash": "hash_rep",
            "kind": "page",
            "doc_type": None,
            "rrf_score": 0.045,
            "text": "Informatii utile",
        },
    ]

    deduped = deduplicate_results(items)
    assert len(deduped) == 1
    assert deduped[0]["chunk_id"] == "p2"
    assert deduped[0]["rrf_score"] == 0.045


def test_deduplicate_results_preserves_unique():
    items = [
        {"chunk_id": "c1", "content_hash": "h1", "kind": "file", "rrf_score": 0.02, "text": "T1"},
        {"chunk_id": "c2", "content_hash": "h2", "kind": "file", "rrf_score": 0.05, "text": "T2"},
        {"chunk_id": "c3", "content_hash": "h3", "kind": "page", "rrf_score": 0.03, "text": "T3"},
    ]

    deduped = deduplicate_results(items, k=2)
    assert len(deduped) == 2
    assert [d["chunk_id"] for d in deduped] == ["c2", "c3"]


def test_rrf_fuse():
    vec = [
        {"chunk_id": "c1", "text": "Doc 1"},
        {"chunk_id": "c2", "text": "Doc 2"},
    ]
    fts = [
        {"chunk_id": "c2", "text": "Doc 2"},
        {"chunk_id": "c3", "text": "Doc 3"},
    ]
    fused = rrf_fuse(vec, fts, k=60)
    assert len(fused) == 3
    # c2 is in both, so it has higher RRF score than c1 and c3
    assert fused[0]["chunk_id"] == "c2"
    assert fused[0]["vec_rank"] == 2
    assert fused[0]["fts_rank"] == 1
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]
