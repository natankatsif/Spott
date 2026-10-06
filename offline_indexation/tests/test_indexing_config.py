"""Tests for indexing/__main__.py argument parsing and stale file cleanup."""

import json

from indexing.__main__ import chunk_all, parse_args


def test_parse_args_defaults():
    args = parse_args([])
    assert args.from_jsonl is False
    assert args.clean_orphans is True


def test_parse_args_from_jsonl():
    args = parse_args(["--from-jsonl"])
    assert args.from_jsonl is True
    assert args.clean_orphans is True


def test_parse_args_no_clean_orphans():
    args = parse_args(["--no-clean-orphans"])
    assert args.clean_orphans is False

    args2 = parse_args(["--clean-orphans"])
    assert args2.clean_orphans is True


def test_chunk_all_removes_stale_jsonl(tmp_path):
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()

    # Create one valid parsed doc
    doc1 = {
        "sha256": "doc1_active",
        "metadata": {"title": "Active Document"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Acesta este un document activ pentru verificare.", "section": [], "lang": "ro"}
        ],
    }
    (parsed_dir / "doc1.json").write_text(json.dumps(doc1), encoding="utf-8")

    # Create a stale .jsonl in chunks_dir from a deleted document
    stale_file = chunks_dir / "file_deleted_doc.jsonl"
    stale_file.write_text('{"doc_id": "file:deleted_doc"}\n', encoding="utf-8")
    assert stale_file.exists()

    by_doc = chunk_all(parsed_dir, chunks_dir)
    assert "file:doc1_active" in by_doc

    # Stale file must be unlinked
    assert not stale_file.exists()
    # Active file must exist
    assert (chunks_dir / "file_doc1_active.jsonl").exists()
