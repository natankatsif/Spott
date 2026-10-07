"""Contact cards (docs/history/tasks/09 §4): phones only from lines and only real numbers, names of departments."""

from spott.ingest.contacts.extract import contact_from_chunk, dedupe, institution_in, phones_in, slug_name

CHUNK = {"chunk_id": "c1", "doc_id": "page:mobilitatechisinau.md/audienta", "site": "mobilitatechisinau.md",
         "url": "https://mobilitatechisinau.md/?page_id=6863", "title": "Audiență - mobilitatechisinau.md",
         "category": "mobility"}


def line(i, text):
    return {"line_id": f"l{i}", "text": text}


def test_phones_are_real_numbers_as_written():
    assert phones_in("ANTICAMERA TEL: 022-20-46-90 FAX: 022 -20-46-58") == ["022-20-46-90", "022 -20-46-58"]
    assert phones_in("(022) 528 117 · linia verde 0 8000 12 57 · +373 22 228 110") == [
        "(022) 528 117", "0 8000 12 57", "+373 22 228 110"]
    assert phones_in("Strategia 2013 - 2027, cod fiscal 1007601009484, nr. 000 421-885") == []


def test_card_from_lines():
    lines = [line(0, "MD-2004, municipiul Chișinău, Str. Serghei Lazo, 18"),
             line(1, "Direcția Generală Mobilitate Urbană este o subdiviziune a Consiliului Municipal Chișinău, ce are "
                     "în sarcina sa dezvoltarea infrastructurii urbane."),
             line(2, "Program: luni - vineri, 08:00 - 17:00"),
             line(3, "ANTICAMERA TEL: 022-20-46-90 EMAIL: dirtrans@pmc.md")]
    card = contact_from_chunk(CHUNK, lines, lines)
    assert card["name"] == "Direcția Generală Mobilitate Urbană"
    assert (card["phone"], card["email"]) == (["022-20-46-90"], ["dirtrans@pmc.md"])
    assert card["address"] == "MD-2004, municipiul Chișinău, Str. Serghei Lazo, 18"
    assert card["hours"] == "Program: luni - vineri, 08:00 - 17:00"
    assert card["area"].startswith("Direcția Generală Mobilitate Urbană este o subdiviziune")
    assert card["line_ids"] == ["l3", "l0", "l2"]  # every phone, e-mail, address and hours is in these lines
    assert card["is_general"] is False
    assert contact_from_chunk(CHUNK, [line(0, "Fără date de contact.")], []) is None


def test_same_footer_on_many_pages_is_one_card():
    footer = [line(0, "TEL: 022-20-46-90 EMAIL: dirtrans@pmc.md")]
    cards = [contact_from_chunk(CHUNK | {"url": f"https://mobilitatechisinau.md/?p={i}"}, footer, footer) for i in range(3)]
    cards.append(contact_from_chunk(CHUNK | {"url": "https://mobilitatechisinau.md/contacte"}, footer, footer))
    assert [c["url"] for c in dedupe(cards)] == ["https://mobilitatechisinau.md/contacte"]


def test_names():
    assert institution_in("Direcția Generală Arhitectură, Urbanism și Relații Funciare este") == \
        "Direcția Generală Arhitectură, Urbanism și Relații Funciare"
    assert institution_in("Primăria Municipiului Chișinău informează că astăzi") == "Primăria Municipiului Chișinău"
    assert institution_in("Serviciul social dispune de 10 centre") is None
    assert slug_name("https://proiecte.chisinau.md/ro/centrul-de-zi-pentru-copii-si-familii") == \
        "Centrul de zi pentru copii si familii"
    general = contact_from_chunk(CHUNK, [line(0, "Primăria municipiului Chișinău, tel. 022 20 17 07")], [])
    assert general["is_general"] is True
