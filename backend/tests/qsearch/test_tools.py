"""Tests for retrieval tools (search, grep, toc, open), escaping, and toc tree assembly."""

import pytest

from spott.core.search import ilike_contains
from spott.qsearch.tools import (
    build_toc_nodes,
    enforce_char_limit,
    grep_tool,
    open_tool,
    search_tool,
    toc_tool,
)


def test_ilike_contains_takes_wildcards_literally():
    assert ilike_contains("abc") == "%abc%"
    assert ilike_contains("100% pure") == r"%100\% pure%"
    assert ilike_contains("a_b_c") == r"%a\_b\_c%"
    assert ilike_contains(r"path\to\file") == r"%path\\to\\file%"
    assert ilike_contains(r"100%_val\test") == r"%100\%\_val\\test%"


def test_enforce_char_limit():
    data = {
        "items": [{"id": i, "text": "x" * 100} for i in range(10)],
    }
    # When limit is plenty
    limited = enforce_char_limit(dict(data), 5000, "items")
    assert len(limited["items"]) == 10
    assert "truncated" not in limited

    # When limit is strict
    limited = enforce_char_limit(dict(data), 300, "items")
    assert len(limited["items"]) < 10
    assert limited.get("truncated") is True


def test_build_toc_nodes():
    chunks = [
        {
            "chunk_id": "c2",
            "ord": 10,
            "section": ["Capitolul II"],
            "legal_path": ["art. 2"],
            "line_count": 5,
        },
        {
            "chunk_id": "c1",
            "ord": 1,
            "section": ["Capitolul I"],
            "legal_path": ["art. 1"],
            "line_count": 3,
        },
        {
            "chunk_id": "c0",
            "ord": 0,
            "citation_label": "Preambul",
            "line_count": 2,
        },
    ]

    nodes = build_toc_nodes(chunks)
    assert len(nodes) == 3
    # Order by ord
    assert [n["node_id"] for n in nodes] == ["c0", "c1", "c2"]
    assert nodes[0]["title"] == "Preambul"
    assert nodes[1]["title"] == "Capitolul I › art. 1"
    assert nodes[2]["title"] == "Capitolul II › art. 2"


# --- DB tests ----------------------------------------------------------------


@pytest.mark.db
def test_live_tools():
    from spott.core.db import get_connection

    try:
        conn = get_connection()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    try:
        # 1. Search tool
        res = search_tool(conn, "autorizatie", k=3)
        assert "items" in res
        assert "timings_ms" in res
        assert len(res["items"]) <= 3

        # 2. Grep tool
        grep_res = grep_tool(conn, "Chișinău", limit=5)
        assert "lines" in grep_res
        assert len(grep_res["lines"]) <= 5
        for l in grep_res["lines"]:
            assert "line_id" in l
            assert "deep_link" in l
            assert l["deep_link"].startswith("http")

        # 3. Find a doc_id to test toc and open
        with conn.cursor() as cur:
            cur.execute("SELECT doc_id FROM chunks LIMIT 1")
            row = cur.fetchone()
            doc_id = row[0] if row else None

        if doc_id:
            toc_res = toc_tool(conn, doc_id)
            assert toc_res["doc_id"] == doc_id
            assert "nodes" in toc_res
            assert len(toc_res["nodes"]) > 0

            open_res = open_tool(conn, doc_id, max_lines=10)
            assert open_res["doc_id"] == doc_id
            assert len(open_res["lines"]) <= 10
            for l in open_res["lines"]:
                assert "line_id" in l
                assert "deep_link" in l
    finally:
        conn.close()
