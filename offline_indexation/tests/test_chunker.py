"""Tests for chunking/chunker.py: merging, splitting, tables with repeating headers, boundary rules."""

import pytest
from chunking.chunker import chunk_document, chunk_table_block, split_long_text


def test_merge_short_blocks():
    """Adjacent short blocks of the same parent should be merged into one chunk."""
    doc = {
        "sha256": "abc1234",
        "metadata": {"title": "Decizia nr. 1"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Paragraf 1 scurt.", "section": ["Sec1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Paragraf 2 scurt.", "section": ["Sec1"], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "Paragraf 3 scurt.", "section": ["Sec1"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "Paragraf 1 scurt." in chunks[0]["text"]
    assert "Paragraf 2 scurt." in chunks[0]["text"]
    assert "Paragraf 3 scurt." in chunks[0]["text"]
    assert chunks[0]["block_ids"] == [0, 1, 2]


def test_no_merge_across_legal_boundary():
    """Blocks from different articles/points must NOT be merged together."""
    doc = {
        "sha256": "abc1234",
        "metadata": {"title": "Decizia nr. 1", "doc_type": "decizie", "number": "1", "date": "2023-01-01"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Articolul 1. Primul articol.", "section": [], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Text in cadrul primului articol.", "section": [], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "Articolul 2. Al doilea articol.", "section": [], "lang": "ro"},
            {"id": 3, "type": "paragraph", "text": "Text in cadrul celui de-al doilea articol.", "section": [], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    assert "Articolul 1" in chunks[0]["text"]
    assert "Articolul 2" not in chunks[0]["text"]
    assert "Articolul 2" in chunks[1]["text"]
    assert chunks[0]["legal_path"] == ["Articolul 1"]
    assert chunks[1]["legal_path"] == ["Articolul 2"]


def test_split_long_text():
    """Text exceeding max_size (>2500) must be split into pieces with overlap."""
    sentence = "Aceasta este o propoziție lungă cu informații importante pentru cetățeni. "
    long_text = sentence * 100  # ~7300 chars
    parts = split_long_text(long_text, target_size=1500, max_size=2500, overlap=200)

    assert len(parts) >= 3
    for p in parts:
        assert len(p) <= 2500
    # Overlap test: end of part 0 should appear in part 1
    overlap_sample = parts[0][-100:]
    assert overlap_sample in parts[1] or parts[0][-50:] in parts[1]


def test_table_repeats_header():
    """Large tables must be split across chunks with table header repeated in each."""
    header = ["Număr", "Denumire serviciu", "Taxă (MDL)"]
    rows = [[str(i), f"Serviciul public numărul {i} prestat de primărie", f"{i * 50}"] for i in range(1, 80)]
    table_block = {
        "id": 0,
        "type": "table",
        "header": header,
        "rows": rows,
        "section": ["Servicii"],
        "lang": "ro",
    }
    chunks = chunk_table_block(table_block, target_size=1500)
    assert len(chunks) > 1

    for c in chunks:
        # Every chunk must contain the markdown header
        assert "| Număr | Denumire serviciu | Taxă (MDL) |" in c
        assert "| --- | --- | --- |" in c


def test_no_merge_across_languages():
    """Blocks in different languages must never be merged."""
    doc = {
        "sha256": "lang123",
        "metadata": {"title": "Bilingual Doc"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Text în limba română.", "section": ["A"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Текст на русском языке.", "section": ["A"], "lang": "ru"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    assert chunks[0]["lang"] == "ro"
    assert chunks[1]["lang"] == "ru"


def test_stable_chunk_id():
    """Identical document structure must produce identical chunk_id."""
    doc = {
        "sha256": "fixedsha",
        "metadata": {"title": "Doc"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Continut neschimbat.", "section": [], "lang": "ro"},
        ],
    }
    chunks1 = chunk_document(doc)
    chunks2 = chunk_document(doc)
    assert chunks1[0]["chunk_id"] == chunks2[0]["chunk_id"]


def test_bboxes_and_pages_preserved():
    """Bounding boxes and page numbers from blocks must be present in chunks."""
    doc = {
        "sha256": "bbox_doc",
        "metadata": {"title": "Doc with bboxes"},
        "blocks": [
            {
                "id": 0,
                "type": "paragraph",
                "text": "Text cu coordonate.",
                "page": 2,
                "bboxes": [{"page": 2, "l": 10.0, "t": 20.0, "r": 100.0, "b": 150.0, "origin": "BOTTOMLEFT"}],
                "section": [],
                "lang": "ro",
            }
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0]["pages"] == [2]
    assert len(chunks[0]["bboxes"]) == 1
    assert chunks[0]["bboxes"][0]["page"] == 2
    assert chunks[0]["bboxes"][0]["l"] == 10.0
