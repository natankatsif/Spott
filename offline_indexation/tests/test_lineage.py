"""Act references and their relation, on phrasings from the real corpus."""

from lineage.extract import find_references


def refs(text):
    return [(r.doc_type, r.number, r.date, r.relation) for r in find_references(text)]


def test_amendment():
    text = ("Se operează modificări în textul deciziei Consiliului municipal Chișinău nr. 4/1 din 05.03.2020 "
            "„Cu privire la elaborarea strategiei de dezvoltare\"")
    assert refs(text) == [("decizie", "4/1", "2020-03-05", "amends")]


def test_preamble_only_refers_even_when_it_mentions_amendments():
    text = ("Având în vedere decizia Consiliului Municipal Chișinău nr.\n12/14 din 28.07.2020 „Cu privire la "
            "aprobarea Planului\", operarea unor modificări în decizia Consiliului Municipal Chișinău nr.\n"
            "4/1 din 05.03.2020 și Tema Program")
    assert refs(text) == [("decizie", "12/14", "2020-07-28", "refers"), ("decizie", "4/1", "2020-03-05", "refers")]


def test_preamble_under_a_title_without_final_period():
    text = ("DECIZIE Cu privire la Caietul de sarcini pentru elaborarea Planului urbanistic general\n"
            "Având în vedere decizia Consiliului Municipal Chișinău nr.\n12/14 din 28.07.2020, operarea unor "
            "modificări în decizia Consiliului Municipal Chișinău nr.\n4/1 din 05.03.2020 și pct.\n4, faza 6 din "
            "anexa nr. 1 la decizia sus-menționată, în conformitate cu prevederile Legii nr.\n835/1996")
    assert refs(text) == [("decizie", "12/14", "2020-07-28", "refers"), ("decizie", "4/1", "2020-03-05", "refers")]


def test_group_ended_without_nr():
    text = ("6. Grupul de lucru privind supravegherea procesului de elaborare a Planului Urbanistic General, "
            "aprobată prin Dispoziția 185-d din 23.04.2020, își încetează activitatea.")
    assert refs(text) == [("dispozitie", "185-d", "2020-04-23", "repeals")]


def test_repeal_and_ocr_glued_date():
    assert refs("3. Se abrogă decizia nr. 6/19-15 din 26.05.2020 „Cu privire la aprobarea Statutului\"") == [
        ("decizie", "6/19-15", "2020-05-26", "repeals")]
    assert refs("Dispoziției Primarului General al municipiului Chișinău nr. 366-ddin 09 octombrie 2025") == [
        ("dispozitie", "366-d", "2025-10-09", "refers")]


def test_sentences_are_separate():
    text = ("1. Se abrogă decizia nr. 3/2 din 01.02.2019. 2. Controlul executării deciziei nr. 5/7 revine "
            "viceprimarului.")
    assert refs(text) == [("decizie", "3/2", "2019-02-01", "repeals"), ("decizie", "5/7", None, "refers")]


def test_not_a_reference():
    assert refs("Hotărârea din 2020 a fost publicată. Decizia sus-menționată se aplică.") == []


def test_russian():
    assert refs("Признать утратившим силу решение Муниципального совета № 4/1 от 05.03.2020.") == [
        ("decizie", "4/1", "2020-03-05", "repeals")]
