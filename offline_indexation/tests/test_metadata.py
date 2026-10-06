"""Act number and date come from the act's own title block and file name, never from references in the body."""

from parsing.metadata import extract, find_date, title_block


def blocks(*texts, kind="paragraph"):
    return [{"type": kind, "text": t} for t in texts]


def src(file_name, anchor=""):
    return [{"url": f"https://dgaurf.md/storage/{file_name}", "anchor_text": anchor}]


def test_date_from_file_name_not_from_a_contract_cited_in_the_body():
    m = extract(
        blocks("PRIMAR GENERAL AL MUNICIPIULUI CHIȘINĂU",
               "DISPOZIȚIE Cu privire la consultările publice (CP-03) privind Studiile de fundamentare",
               "În scopul organizării … contractului nr. 41/26 din 20.05.2026 „Privind achiziționarea"),
        src("dispozitia-nr.-373-d-din-25-august-2026.pdf"),
    )
    assert (m["doc_type"], m["number"], m["date"], m["effective_date"]) == ("dispozitie", "373-d", "2026-08-25",
                                                                            "2026-08-25")


def test_code_cited_by_a_regulation_is_not_its_number():
    m = extract(
        blocks("REGULAMENT cu privire la organizarea și funcționarea Grupului de supraveghere",
               "Articolul 2. Cadrul legal",
               "· Codul urbanismului și construcțiilor al Republicii Moldova nr. 434/2023;"),
        src("regulament-gs.pdf", "Regulamentul Grupului de supraveghere"),
    )
    assert (m["doc_type"], m["number"], m["date"]) == ("regulament", None, None)


def test_number_written_as_in_the_head_when_digits_agree_with_the_file_name():
    m = extract(
        blocks("n. 12/14", "REPUBLICA MOLDOVA") + blocks("DECIZIE", kind="heading")
        + blocks("Cu privire la aprobarea Planului de acțiuni", "nr. 4/1 din 05.03.2020 și Tema-program"),
        src("decizie-1214-din-28.07.2020-(1).pdf", "Decizia CMC privind elaborarea PUG"),
    )
    assert (m["number"], m["date"]) == ("12/14", "2020-07-28")


def test_ocr_number_in_the_head_loses_to_the_file_name():
    m = extract(blocks("nr. 3.51-d", "DISPOZIȚIE", "Cu privire la … Contractului nr. 45/25 din 16 iunie 2025"),
                src("dispozitia-nr.-251-d-din-02.07.2026.pdf"))
    assert (m["number"], m["date"]) == ("251-d", "2026-07-02")


def test_own_number_and_date_in_the_title_line():
    m = extract(blocks("DECIZIE nr. 5/12 din 3 martie 2024 cu privire la taxe", "1. Se aprobă taxa din 01.01.2025."),
                src("doc.pdf"))
    assert (m["doc_type"], m["number"], m["date"]) == ("decizie", "5/12", "2024-03-03")


def test_title_block_stops_at_the_body():
    assert title_block(blocks("DISPOZIȚIE", "nr. 7-d", "1. Se aprobă nr. 99 din 01.01.2020")) == "DISPOZIȚIE\nnr. 7-d"


def test_dates_in_file_names():
    assert find_date("dispozitia-nr.-373-d-din-25-august-2026") == "2026-08-25"
    assert find_date("decizia_nr_79_din_27_iulie_2021") == "2021-07-27"
    assert find_date("decizie-1214-din-28.07.2020") == "2020-07-28"
