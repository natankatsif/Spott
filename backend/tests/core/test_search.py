"""Pure unit tests for search: FTS query building, deduplication, and RRF fusion without DB or models."""

from spott.core.search import build_fts_query, deduplicate_results, weighted_rrf_fuse


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


def test_rrf_fuse_rewards_agreement_between_rankings():
    vec = [
        {"chunk_id": "c1", "text": "Doc 1"},
        {"chunk_id": "c2", "text": "Doc 2"},
    ]
    fts = [
        {"chunk_id": "c2", "text": "Doc 2"},
        {"chunk_id": "c3", "text": "Doc 3"},
    ]
    fused = weighted_rrf_fuse([(vec, 1.0, "vec_rank"), (fts, 1.0, "fts_rank")], k=60)
    assert len(fused) == 3
    # c2 is in both, so it has higher RRF score than c1 and c3
    assert fused[0]["chunk_id"] == "c2"
    assert fused[0]["vec_rank"] == 2
    assert fused[0]["fts_rank"] == 1
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]
    assert {r["chunk_id"]: r["fts_rank"] for r in fused}["c1"] is None


def test_weighted_rrf_fuse():
    vec = [{"chunk_id": "c1"}, {"chunk_id": "c2"}]
    line = [{"chunk_id": "c2"}, {"chunk_id": "c3"}]
    fts = [{"chunk_id": "c3"}]

    # When vec has weight 2.0 and fts has weight 0.1
    fused = weighted_rrf_fuse(
        [(vec, 2.0, "vec_rank"), (line, 1.0, "line_rank"), (fts, 0.1, "fts_rank")],
        k=60,
    )
    assert len(fused) == 3
    assert fused[0]["chunk_id"] in ("c1", "c2")


def test_make_deep_link_pdf():
    from spott.core.links import make_deep_link

    url = "https://site.md/doc.pdf"
    assert make_deep_link(url, "some line text", page=5) == "https://site.md/doc.pdf#page=5"
    assert make_deep_link(url, "some line text", page=None) == "https://site.md/doc.pdf#page=1"


def test_make_deep_link_html():
    from spott.core.links import make_deep_link

    url = "https://site.md/page/about"
    text = "Regulamentul privind acordarea autorizațiilor de construire în municipiul Chișinău"
    link = make_deep_link(url, text)
    assert link.startswith("https://site.md/page/about#:~:text=")
    assert "Regulamentul" in link
    assert "municipiul" in link
    assert "autoriza%C8%9B" in link


def test_all_lines_in_db_have_url_and_deep_link():
    import pytest

    from spott.core.db import get_connection
    from spott.core.links import make_deep_link

    try:
        conn = get_connection()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.line_id, l.text, l.page, c.url, c.found_on, c.kind
                FROM lines l
                JOIN chunks c ON l.chunk_id = c.chunk_id
                LIMIT 1000
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        pytest.skip("The configured database has no index")
    for line_id, text, page, url, found_on, kind in rows:
        assert url, f"Line {line_id} missing url"
        dl = make_deep_link(url, text, page)
        assert dl, f"Line {line_id} missing deep_link"
        if kind == "file":
            assert page is not None, f"File line {line_id} missing page"
            assert found_on is not None, f"File line {line_id} missing found_on"
