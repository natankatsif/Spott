"""Tests for common/text.py and indexing/embeddings.py."""

from retrieval.embeddings import get_device

from common.text import check_contacts, format_table_markdown, has_contacts


def test_has_contacts():
    assert has_contacts("Pentru informații apelați +373 22 201 601 sau scrieți la dgaurf@pmc.md")
    assert has_contacts("Contact: 022-20-16-01")
    assert has_contacts("Email: primar@chisinau.md")
    assert not has_contacts("Aceasta este o decizie standard fără contacte.")
    # check_contacts is alias
    assert check_contacts("Contact: 022 123 456")


def test_format_table_markdown():
    header = ["N", "Serviciu", "Taxa"]
    rows = [["1", "Certificat", "50 MDL"], ["2", "Autorizatie", "100 MDL"]]
    md = format_table_markdown(header, rows)
    assert "| N | Serviciu | Taxa |" in md
    assert "| --- | --- | --- |" in md
    assert "| 1 | Certificat | 50 MDL |" in md
    assert "| 2 | Autorizatie | 100 MDL |" in md

    # Empty table
    assert format_table_markdown([], []) == ""


def test_get_device():
    dev = get_device()
    assert dev in ("mps", "cuda", "cpu")
