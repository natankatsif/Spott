"""Pure helpers of indexing.indexer and indexing.search (no database, no models)."""

import json

from spott.core.search import rrf_fuse
from spott.ingest.chunking.chunker import starts_new_group
from spott.ingest.indexing.indexer import CHUNK_COLUMNS, UPSERT_CHUNK, UPSERT_DOCUMENT, document_record, row_values


def test_upsert_sql_updates_every_non_key_column():
    assert UPSERT_CHUNK.count("%s") == len(CHUNK_COLUMNS)
    assert "ON CONFLICT (chunk_id)" in UPSERT_CHUNK
    assert "chunk_id = EXCLUDED.chunk_id" not in UPSERT_CHUNK
    assert "embedding = EXCLUDED.embedding" in UPSERT_CHUNK
    assert "indexed_at = EXCLUDED.indexed_at" in UPSERT_DOCUMENT
    assert "NOW()" in UPSERT_DOCUMENT


def test_row_values_encodes_json_and_bools():
    chunk = {"chunk_id": "c1", "doc_id": "d1", "legal_path": ["Anexa nr. 1", "pct. 2"], "has_contacts": None}
    values = dict(zip(CHUNK_COLUMNS, row_values(chunk, CHUNK_COLUMNS), strict=True))
    assert json.loads(values["legal_path"]) == ["Anexa nr. 1", "pct. 2"]
    assert json.loads(values["bboxes"]) == []
    assert values["has_contacts"] is False
    assert values["embedding"] is None


def test_row_values_ord_falls_back_to_first_block():
    def ord_of(chunk):
        return dict(zip(CHUNK_COLUMNS, row_values(chunk, CHUNK_COLUMNS), strict=True))["ord"]

    assert ord_of({"chunk_id": "c1", "block_ids": [7, 8]}) == 7  # jsonl written before `ord` existed
    assert ord_of({"chunk_id": "c1", "block_ids": [7, 8], "ord": 3}) == 3
    assert ord_of({"chunk_id": "c1", "block_ids": []}) == 0


def test_document_record_takes_metadata_from_first_chunk():
    record = document_record("file:abc", {"title": "Decizia", "site": "dgaurf.md", "text": "ignored"})
    assert record == {"kind": "file", "title": "Decizia", "site": "dgaurf.md", "doc_id": "file:abc"}


def test_rrf_fuse_rewards_agreement_between_rankings():
    vec = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    fts = [{"chunk_id": "b"}, {"chunk_id": "c"}]
    fused = rrf_fuse(vec, fts)
    assert [r["chunk_id"] for r in fused][0] == "b"
    b = fused[0]
    assert (b["vec_rank"], b["fts_rank"]) == (2, 1)
    assert {r["chunk_id"]: r["fts_rank"] for r in fused}["a"] is None


def test_heading_attaches_to_following_content():
    heading = {"type": "heading", "text": "Capitolul I", "section": []}
    para = {"type": "paragraph", "text": "Text", "section": ["Capitolul I"], "lang": "ro"}
    assert not starts_new_group([heading], 12, para)
    assert starts_new_group([heading, para], 20, {"type": "heading", "text": "Capitolul II", "section": []})


class FakeCursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 3

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


def indexer_with(conn):
    from spott.ingest.indexing.indexer import Indexer

    idx = Indexer.__new__(Indexer)  # no model, no device: only the SQL is under test
    idx.conn = conn
    return idx


def test_a_site_run_removes_that_sites_documents_it_no_longer_has():
    conn = FakeConn()
    assert indexer_with(conn).clean_orphaned_documents({"page:a.md/x"}, sites=["a.md"]) == 3
    [(sql, params)] = conn.log
    assert "site = ANY" in sql and params == (["a.md"], ["page:a.md/x"])


def test_a_site_with_nothing_left_is_emptied_but_an_empty_full_run_wipes_nothing():
    conn = FakeConn()
    indexer_with(conn).clean_orphaned_documents(set(), sites=["gone.md"])
    assert conn.log[0][1] == (["gone.md"], [])
    conn = FakeConn()
    assert indexer_with(conn).clean_orphaned_documents(set()) == 0 and conn.log == []


def test_doc_site_of_pages_and_files():
    from spott.ingest.indexing.__main__ import doc_site

    assert doc_site({"kind": "page", "site": "a.md"}) == "a.md"
    assert doc_site({"sources": [{"site": "b.md", "url": "https://b.md/x.pdf"}]}) == "b.md"
    assert doc_site({"sources": []}) is None
