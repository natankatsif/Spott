"""Tests for chunking/legal.py: legal hierarchy matching, stack popping, false positive rejection."""

import pytest

from chunking.legal import LegalHierarchyTracker, LegalLevel, match_legal_item


@pytest.mark.parametrize(
    "text,expected_level,expected_label",
    [
        # Romanian tests
        ("Anexa nr. 1 la decizia nr. 12/3", LegalLevel.ANEXA, "Anexa nr. 1"),
        ("Anexa 2 la Regulament", LegalLevel.ANEXA, "Anexa nr. 2"),
        ("Capitolul II. Dispoziții generale", LegalLevel.CAPITOL, "Capitolul II"),
        ("Secțiunea 1. Noțiuni principale", LegalLevel.SECTIUNE, "Secțiunea 1"),
        ("Articolul 4. Competențele autorităților publice", LegalLevel.ARTICOL, "Articolul 4"),
        ("Art. 15. Drepturile funcționarului", LegalLevel.ARTICOL, "Art. 15"),
        ("pct. 12. Se aprobă componența comisiei", LegalLevel.PUNCT, "pct. 12"),
        ("12. Se aprobă regulamentul...", LegalLevel.PUNCT, "pct. 12"),
        ("5.2. Cererea se depune personal sau online", LegalLevel.PUNCT, "pct. 5.2"),
        ("alin. (2) În cazul în care solicitantul...", LegalLevel.ALINEAT, "alin. (2)"),
        ("(3) Termenul de examinare este de 10 zile", LegalLevel.ALINEAT, "alin. (3)"),
        ("lit. a) copia buletinului de identitate", LegalLevel.LITERA, "lit. a)"),
        ("b) dovada achitării taxei de stat", LegalLevel.LITERA, "lit. b)"),
        # Russian tests
        ("Приложение № 1 к распоряжению примара", LegalLevel.ANEXA, "Приложение № 1"),
        ("Глава III. Права и обязанности сторон", LegalLevel.CAPITOL, "Глава III"),
        ("Раздел 2. Финансирование и бюджет", LegalLevel.SECTIUNE, "Раздел 2"),
        ("Статья 10. Порядок подачи заявлений", LegalLevel.ARTICOL, "Статья 10"),
        ("Ст. 5. Ответственность должностных лиц", LegalLevel.ARTICOL, "Ст. 5"),
        ("пункт 7. Контроль возложить на вице-примара", LegalLevel.PUNCT, "п. 7"),
        ("п. 4. Настоящее решение вступает в силу...", LegalLevel.PUNCT, "п. 4"),
        ("часть (2) Документы представляются в двух экземплярах", LegalLevel.ALINEAT, "ч. (2)"),
        ("подпункт a) заявление установленного образца", LegalLevel.LITERA, "подп. a)"),
    ],
)
def test_match_legal_item_positive(text, expected_level, expected_label):
    match = match_legal_item(text)
    assert match is not None
    level, label = match
    assert level == expected_level
    assert label == expected_label


@pytest.mark.parametrize(
    "text",
    [
        "În conformitate cu art. 14 din Legea nr. 436/2006",
        "în conformitate cu art. 14",
        "Conform pct. 3 din decizia sus-menționată",
        "Art. 14 din Legea nr. 436/2006 privind administrația publică",
        "Ст. 14 Закона о местном публичном управлении",
        "10 000 lei se alocă din fondul de rezervă",
        "12.03.2023 a fost aprobat proiectul",
        "01.09.2025 intră în vigoare decizia",
        "Simpla propoziție fără elemente juridice.",
    ],
)
def test_match_legal_item_false_positives(text):
    assert match_legal_item(text) is None


def test_hierarchy_stack_progression():
    tracker = LegalHierarchyTracker()
    blocks = [
        {"text": "DECIZIE Cu privire la aprobare"},
        {"text": "Anexa nr. 1 Regulamentul cu privire la parcare"},
        {"text": "Capitolul I. Dispoziții generale"},
        {"text": "Articolul 1. Obiectul regulamentului"},
        {"text": "pct. 1. Prezentul regulament stabilește regulile."},
        {"text": "alin. (1) Parcarea este permisă doar în locurile marcate."},
        {"text": "lit. a) pe străzile magistrale;"},
        {"text": "lit. b) în parcările special amenajate."},
        {"text": "alin. (2) Se interzice parcarea pe trotuar."},
        {"text": "pct. 2. Tarifele sunt aprobate anual."},
        {"text": "Articolul 2. Sancțiuni"},
        {"text": "Anexa nr. 2 Tarifele aplicate"},
        {"text": "1. Tariful pe oră este de 10 lei."},
    ]

    paths = [tracker.process_block(b) for b in blocks]

    assert paths[0] == []
    assert paths[1] == ["Anexa nr. 1"]
    assert paths[2] == ["Anexa nr. 1", "Capitolul I"]
    assert paths[3] == ["Anexa nr. 1", "Capitolul I", "Articolul 1"]
    assert paths[4] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 1"]
    assert paths[5] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 1", "alin. (1)"]
    assert paths[6] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 1", "alin. (1)", "lit. a)"]
    # lit. b) pops lit. a)
    assert paths[7] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 1", "alin. (1)", "lit. b)"]
    # alin. (2) pops lit. b) and alin. (1)
    assert paths[8] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 1", "alin. (2)"]
    # pct. 2 pops alin. (2) and pct. 1
    assert paths[9] == ["Anexa nr. 1", "Capitolul I", "Articolul 1", "pct. 2"]
    # Articolul 2 pops pct. 2 and Articolul 1
    assert paths[10] == ["Anexa nr. 1", "Capitolul I", "Articolul 2"]
    # Anexa nr. 2 pops everything back to level 0
    assert paths[11] == ["Anexa nr. 2"]
    assert paths[12] == ["Anexa nr. 2", "pct. 1"]


def test_heading_resets_punct_and_deeper():
    """A new heading must reset PUNCT and deeper levels in tracker."""
    tracker = LegalHierarchyTracker(allow_sub_articles=True)
    blocks = [
        {"type": "paragraph", "text": "Articolul 5. Procedura de examinare."},
        {"type": "paragraph", "text": "pct. 1. Cererea se depune la sediul instituției."},
        {"type": "paragraph", "text": "alin. (1) Solicitantul anexează actele necesare."},
        # Heading that does not match an article or chapter resets punct and deeper
        {"type": "heading", "text": "Secțiune nouă fără puncte"},
        {"type": "paragraph", "text": "Acest paragraf nu trebuie să moștenească pct. 1 sau alin. (1)."},
    ]
    paths = [tracker.process_block(b) for b in blocks]
    assert paths[0] == ["Articolul 5"]
    assert paths[1] == ["Articolul 5", "pct. 1"]
    assert paths[2] == ["Articolul 5", "pct. 1", "alin. (1)"]
    assert paths[3] == ["Articolul 5"]
    assert paths[4] == ["Articolul 5"]


def test_numbered_report_without_doc_type():
    """A report without doc_type and without Art/Cap/Anexa must NOT treat numbered lists as pct."""
    from chunking.chunker import chunk_document

    doc = {
        "sha256": "report_123",
        "metadata": {"title": "Raport de activitate privind gestionarea deșeurilor"},  # doc_type is None
        "blocks": [
            {"id": 0, "type": "heading", "text": "Raport anual", "section": [], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "1. În primul semestru au fost evacuate 500 tone de deșeuri solide.", "section": ["Raport anual"], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "Această activitate s-a desfășurat conform graficului aprobat de întreprindere.", "section": ["Raport anual"], "lang": "ro"},
            {"id": 3, "type": "paragraph", "text": "2. Modernizarea parcului de autospeciale a continuat în trimestrul trei.", "section": ["Raport anual"], "lang": "ro"},
            {"id": 4, "type": "paragraph", "text": "Au fost achiziționate cinci unități noi de transport specializat.", "section": ["Raport anual"], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) > 0
    for chunk in chunks:
        assert not any("pct." in p for p in chunk["legal_path"])
        assert "pct." not in chunk["citation_label"]


def test_decision_with_art_and_pct():
    """A decision with doc_type and Art./pct. maintains legal paths as expected."""
    from chunking.chunker import chunk_document

    doc = {
        "sha256": "decizie_456",
        "metadata": {
            "title": "Cu privire la aprobarea bugetului",
            "doc_type": "decizie",
            "number": "12/5",
            "date": "2024-02-15",
        },
        "blocks": [
            {"id": 0, "type": "paragraph", "text": "Articolul 1. Se aprobă bugetul municipal pe anul 2024.", "section": [], "lang": "ro"},
            {"id": 1, "type": "paragraph", "text": "pct. 1. Veniturile se stabilesc în sumă de 7 miliarde lei.", "section": [], "lang": "ro"},
            {"id": 2, "type": "paragraph", "text": "alin. (1) Impozitele directe constituie baza formării bugetului.", "section": [], "lang": "ro"},
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) > 0
    # Legal path must be populated with Articolul 1
    assert any("Articolul 1" in c["legal_path"] for c in chunks)
    assert any("Decizie" in c["citation_label"] for c in chunks)
