"""Tests for indexing/__main__.py argument parsing and chunking of the current documents."""

import json

from spott.ingest.common.loader import load_active_documents
from spott.ingest.indexing.__main__ import chunk_documents, parse_args


def test_parse_args_defaults():
    args = parse_args([])
    assert args.clean_orphans is True


def test_parse_args_no_clean_orphans():
    args = parse_args(["--no-clean-orphans"])
    assert args.clean_orphans is False

    args2 = parse_args(["--clean-orphans"])
    assert args2.clean_orphans is True


def test_the_current_documents_are_chunked_in_memory(tmp_path, registry):
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    doc1 = {
        "sha256": "doc1_active",
        "metadata": {"title": "Active Document"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Acesta este un document activ pentru verificare.", "section": [], "lang": "ro"}
        ],
    }
    (parsed_dir / "doc1_active.json").write_text(json.dumps(doc1), encoding="utf-8")
    (parsed_dir / "old_version.json").write_text(json.dumps(doc1 | {"sha256": "old_version"}), encoding="utf-8")
    registry.add_document(key="doc1_active", url="https://a.md/doc1.pdf", site="a.md", extension=".pdf",
                          source={"found_on": "https://a.md/"})
    registry.record_download("doc1_active", sha256="doc1_active", path="raw/doc1.pdf", extension=".pdf",
                             http_status=200, etag=None, last_modified=None)
    registry.mark_parsed("doc1_active", "parsed")

    by_doc = chunk_documents(load_active_documents(tmp_path, registry=registry))

    assert list(by_doc) == ["file:doc1_active"]  # the registry's current version only, not every parsed file
    assert not (tmp_path / "chunks").exists()


def test_a_document_that_fails_to_chunk_is_left_out(monkeypatch):
    from spott.ingest.indexing import __main__ as indexing

    def chunk_document(doc):
        if doc["doc_id"] == "bad":
            raise ValueError("broken blocks")
        return [{"doc_id": doc["doc_id"], "chunk_id": f"{doc['doc_id']}-1"}]

    monkeypatch.setattr(indexing, "chunk_document", chunk_document)
    assert list(indexing.chunk_documents([{"doc_id": "bad"}, {"doc_id": "good"}])) == ["good"]
