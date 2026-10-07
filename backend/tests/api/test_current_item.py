"""What the admin shows about a running stage: the file or address it is on right now (admin.views.current_item)."""

from spott.api.admin.views import current_item


def test_the_file_being_parsed():
    assert current_item(["[11/200] parsed raw/ab/cd.pdf 2.1s",
                         "[12/200] parsed (ocr) raw/ef/tarife-2026.pdf 8.4s"]) == "parsed tarife-2026.pdf"


def test_the_document_being_downloaded():
    assert current_item(["acc.md [12/340] downloaded https://acc.md/files/decizie.pdf"]) == "downloaded decizie.pdf"


def test_the_last_item_wins_and_a_failure_is_named_as_such():
    assert current_item(["[1/9] parsed a.pdf 1s", "[2/9] failed b.pdf 1s"]) == "failed b.pdf"


def test_stages_that_report_counts_instead_of_items_have_nothing_to_show():
    assert current_item(["acc.md: pages=120 docs=8 queue=43", "Indexing 900 chunks from 40 documents"]) is None
    assert current_item([]) is None
