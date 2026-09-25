"""Tests for chunking/chunker.py: merging, splitting, tables with repeating headers, boundary rules."""

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
            {"id": 0, "type": "paragraph", "text": "Acesta este un text complet în limba română pentru document.", "section": ["A"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Это подробный текст на русском языке для проверки документа.", "section": ["A"], "lang": "ru"},
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
            {"id": 0, "type": "paragraph", "text": "Continut neschimbat si stabil pentru testarea identificatorului.", "section": [], "lang": "ro"},
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
                "text": "Text cu coordonate pentru verificare bboxes in cadrul chunking.",
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


def test_heading_sticks_to_next_content():
    """Headings must not form isolated chunks; they attach to the following content."""
    doc = {
        "sha256": "heading_doc",
        "metadata": {"title": "Pagina de servicii"},
        "blocks": [
            {"id": 0, "type": "heading", "text": "Servicii publice", "section": [], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Primăria oferă servicii de eliberare a actelor pentru toți cetățenii municipiului Chișinău.", "section": ["Servicii publice"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "Servicii publice" in chunks[0]["text"]
    assert "Primăria oferă servicii" in chunks[0]["text"]
    assert chunks[0]["section"] == ["Servicii publice"]
    assert "Servicii publice" in chunks[0]["citation_label"]


def test_nested_headings_stick_to_content():
    """Multiple nested headings attach to the content under them."""
    doc = {
        "sha256": "nested_doc",
        "metadata": {"title": "Ghid"},
        "blocks": [
            {"id": 0, "type": "heading", "text": "Urbanism", "section": [], "lang": "ro"},
            {"id": 1, "type": "heading", "text": "Autorizații", "section": ["Urbanism"], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "Pentru construirea unei clădiri este necesară obținerea autorizației de construire.", "section": ["Urbanism", "Autorizații"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "Urbanism" in chunks[0]["text"]
    assert "Autorizații" in chunks[0]["text"]
    assert chunks[0]["section"] == ["Urbanism", "Autorizații"]


def test_short_chunk_merged_with_neighbor():
    """Chunks < 150 chars are merged with neighbors in the same section."""
    doc = {
        "sha256": "short_merge_doc",
        "metadata": {"title": "Doc"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Aceasta este o secțiune principală care conține informații destul de lungi despre regulament.", "section": ["S1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Notă scurtă adițională de text.", "section": ["S1"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "Notă scurtă adițională" in chunks[0]["text"]


def test_short_chunk_not_merged_across_sections():
    """Chunks < 150 chars do not merge across different sections, but are kept if >= 30 chars."""
    doc = {
        "sha256": "cross_sec_doc",
        "metadata": {"title": "Doc"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Acesta este un paragraf lung în prima secțiune a documentului oficial al primăriei.", "section": ["S1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Paragraf scurt dar valid de peste 30 de caractere.", "section": ["S2"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    assert chunks[0]["section"] == ["S1"]
    assert chunks[1]["section"] == ["S2"]


def test_isolated_tail_discarded():
    """Isolated tail < 30 chars without eligible neighbors is discarded."""
    doc = {
        "sha256": "tail_doc",
        "metadata": {"title": "Doc"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Acesta este un paragraf complet și substanțial pentru secțiunea principală a documentului.", "section": ["S1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "Pag. 15", "section": ["S2"], "lang": "ro"},  # < 30 chars, different section
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "Pag. 15" not in chunks[0]["text"]


def test_split_long_text_no_newlines_and_giant_word():
    """Long text without newlines and very long word are split into parts <= MAX_BLOCK_CHARS with overlap, without text loss."""
    # 1. Long text without newlines (many sentences/words separated by spaces only)
    words = [f"cuvânt{i}" for i in range(1500)]
    text_no_newlines = " ".join(words)  # ~13000 chars
    parts = split_long_text(text_no_newlines, target_size=1500, max_size=2500, overlap=200)
    assert len(parts) > 1
    for p in parts:
        assert len(p) <= 2500
    # Coverage: words across the range must be present
    for w in words[::20]:
        assert any(w in p for p in parts)

    # 2. Giant word without spaces or newlines
    giant_word = "A" * 6000
    parts_giant = split_long_text(giant_word, target_size=1500, max_size=2500, overlap=200)
    assert len(parts_giant) >= 3
    for p in parts_giant:
        assert len(p) <= 2500
    # Overlap verification
    for i in range(len(parts_giant) - 1):
        assert parts_giant[i][-100:] in parts_giant[i + 1] or parts_giant[i + 1][:100] in parts_giant[i]


def test_merge_three_short_puncts_into_range():
    """3 consecutive short legal items (< 300 chars) with same parent merge into a single chunk with range."""
    doc = {
        "sha256": "short_pct_doc",
        "metadata": {"title": "Decizia CMC nr. 10", "doc_type": "decizie", "number": "10", "date": "2023-05-12"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Anexa nr. 2", "section": ["Anexa 2"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "2. Achiziția de vehicule pentru transport public.", "section": ["Anexa 2"], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "3. Repararea căilor de acces către parcuri.", "section": ["Anexa 2"], "lang": "ro"},
            {"id": 3, "type": "paragraph", "text": "4. Construcția unei stații noi de pompare.", "section": ["Anexa 2"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    # The 3 puncts (2, 3, 4) should merge into a single range chunk: pct. 2–4
    assert len(chunks) == 1
    c = chunks[0]
    assert c["legal_path"] == ["Anexa nr. 2", "pct. 2–4"]
    assert "2. Achiziția de vehicule" in c["text"]
    assert "3. Repararea căilor" in c["text"]
    assert "4. Construcția unei stații" in c["text"]
    assert "\n" in c["text"]
    assert "pct. 2–4" in c["citation_label"]


def test_long_puncts_not_merged():
    """Legal items >= 300 chars must not be merged with adjacent items."""
    long_text_1 = "1. Punct lung cu detalii tehnice ample: " + ("descriere detaliată a lucrărilor de infrastructură urbană " * 6)  # > 350 chars
    short_text_2 = "2. Punct scurt secundar de verificare operațională."
    doc = {
        "sha256": "long_pct_doc",
        "metadata": {"title": "Decizia nr. 5", "doc_type": "decizie", "number": "5", "date": "2023-01-10"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": long_text_1, "section": ["S1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": short_text_2, "section": ["S1"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    assert chunks[0]["legal_path"] == ["pct. 1"]
    assert chunks[1]["legal_path"] == ["pct. 2"]


def test_different_parents_not_merged():
    """Short items with different parents in legal_path must NOT be merged together."""
    doc = {
        "sha256": "diff_parents_doc",
        "metadata": {"title": "Decizia nr. 8", "doc_type": "decizie", "number": "8", "date": "2023-02-15"},
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Anexa nr. 1", "section": ["A1"], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "1. Punct în prima anexă scurt.", "section": ["A1"], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "Anexa nr. 2", "section": ["A2"], "lang": "ro"},
            {"id": 3, "type": "paragraph", "text": "2. Punct în a doua anexă scurt.", "section": ["A2"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    assert chunks[0]["legal_path"] == ["Anexa nr. 1", "pct. 1"]
    assert chunks[1]["legal_path"] == ["Anexa nr. 2", "pct. 2"]
