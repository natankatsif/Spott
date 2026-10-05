"""Contact cards from indexed lines: who can help when the documents don't answer (docs/history/tasks/09 §4).

Phones and e-mails come only from lines, found with common/text.py's regexes; a phone must look like a
Moldovan number (0 + 8 digits, or +373 + 8 digits), which leaves out years ("2013 - 2027") and fiscal codes.
Only web pages count: scanned PDFs carry lists of participants with private numbers.
"""

import hashlib
import re
from collections import Counter

from spott.ingest.common.text import EMAIL_RE, PHONE_RE

INSTITUTION_WORD = re.compile(
    r"\b(Direcți[ae]|Directia|Serviciul|Pretura|Primăria|Primaria|Consiliul|Întreprinderea|Intreprinderea|Regia|"
    r"Centrul|Agenția|Departamentul|Secția|Inspecția|Biroul|Instituția|Parcul|Asociația|Î\.M\.|"
    r"Дирекция|Главное управление|Управление|Служба|Претура|Примэрия|Мэрия)\b")
# A name goes on while its words are capitalised (or quoted) or connect two such words.
NAME_WORD = re.compile(r"[„\"«]?[A-ZĂÂÎȘȚА-ЯЁ][\w.\-]*[”\"»]?,?$")
CONNECTORS = {"și", "si", "de", "a", "al", "ai", "pentru", "din", "la", "în", "municipiului", "orașului", "mun.", "и",
              "по", "города", "муниципия"}
AREA = re.compile(r"(are în sarcin|este responsabil|se ocupă|gestionează|atribuți|competenț|отвечает за|занимается|"
                  r"обеспечивает)", re.I)
ADDRESS = re.compile(r"(\bMD-?\s?\d{4}\b|\bstr(?:ada|\.)\s|\bbd\.|\bbulevardul\b|\bул\.|\bбул\.)", re.I)
HOURS = re.compile(r"\b(luni|marți|marti|vineri|program|orele|понедельник|пятниц|график|часы)\b.*\d{1,2}[:.]\d{2}",
                   re.I)
GENERAL = re.compile(r"\b(Primăria|Primaria)( municipiului)? Chișinău|\bПримэри[яи]|\bМэри[яи] Кишин", re.I)
TITLE_SUFFIX = re.compile(r"\s*[–—|-]\s*[\w.]+\.md\s*$", re.I)
MAX_FIELD = 200


MORE_DIGITS = re.compile(r"[\s.-]{0,2}\d{2,3}")


def valid_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return (digits.startswith("373") and len(digits) == 11) or (re.match(r"0[1-9]", digits) is not None
                                                               and len(digits) == 9)


def phones_in(text: str) -> list[str]:
    """Phones as written in the line. The regex stops after three groups ("022-20-46" of "022-20-46-90"): a
    match is extended by the digit groups that follow until it has a full number's digits; a "(022)" area code
    keeps its opening bracket."""
    out = []
    for m in PHONE_RE.finditer(text):
        start = m.start() - 1 if m.start() > 0 and text[m.start() - 1] == "(" else m.start()
        end = m.end()
        wanted = 11 if re.sub(r"\D", "", m.group()).startswith("373") else 9
        while len(re.sub(r"\D", "", text[start:end])) < wanted and (more := MORE_DIGITS.match(text, end)):
            end = more.end()
        phone = text[start:end].strip(" .-")
        if valid_phone(phone):
            out.append(phone)
    return out


def emails_in(text: str) -> list[str]:
    return [e.removeprefix("www.") for e in EMAIL_RE.findall(text)]


def sentence_with(pattern: re.Pattern, lines: list[dict]) -> tuple[str, str] | None:
    """(the first sentence matching, its line id)."""
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line["text"]):
            if pattern.search(sentence):
                return sentence.strip()[:MAX_FIELD], line["line_id"]
    return None


def institution_in(text: str) -> str | None:
    """"Direcția Generală Arhitectură, Urbanism și Relații Funciare" out of the sentence that names it."""
    for m in INSTITUTION_WORD.finditer(text):
        words = [m[1]] if " " not in m[1] else m[1].split()
        rest = text[m.end():].split()
        for i, word in enumerate(rest):
            if NAME_WORD.match(word) or (word.lower() in CONNECTORS and i + 1 < len(rest) and NAME_WORD.match(rest[i + 1])):
                words.append(word)
                if word.endswith((".", "”", '"', "»")) and not re.fullmatch(r"\w\.", word):
                    break
            else:
                break
        name = " ".join(words).rstrip(",. ")
        if len(name.split()) >= 2:
            return name[:120]
    return None


def institution(lines: list[dict]) -> str | None:
    return next((name for line in lines if (name := institution_in(line["text"]))), None)


def page_title(title: str | None, site: str) -> str | None:
    title = TITLE_SUFFIX.sub("", title or "").strip(" –—|-")
    return title if title and title.lower() != site.lower() else None


def slug_name(url: str) -> str | None:
    """ "…/centrul-de-zi-pentru-copii-si-familii" → "Centrul de zi pentru copii si familii"."""
    words = [w for w in url.rstrip("/").rsplit("/", 1)[-1].split("-") if w.isalnum()]
    return " ".join(words).capitalize() if len(words) >= 3 else None


def contact_from_chunk(chunk: dict, lines: list[dict], doc_lines: list[dict]) -> dict | None:
    """One card per chunk that has a phone or an e-mail: the department the chunk (or its page) names, what it
    handles, its phones, e-mails, address and hours — each from a line, all those lines in line_ids."""
    phones, emails, used, near, general = [], [], [], None, False
    for i, line in enumerate(lines):
        found_phones, found_emails = phones_in(line["text"]), emails_in(line["text"])
        if found_phones or found_emails:
            phones += found_phones
            emails += found_emails
            used.append(line["line_id"])
            # The department on the phone's own line, or just above it, is the one the phone belongs to.
            near = near or institution(lines[i:i + 1] + lines[max(0, i - 2):i][::-1])
            general = general or bool(found_phones and GENERAL.search(line["text"]))
    if not phones and not emails:
        return None
    address = next(((line["text"][:MAX_FIELD], line["line_id"]) for line in lines if ADDRESS.search(line["text"])),
                   None)
    hours = sentence_with(HOURS, lines)
    area = sentence_with(AREA, lines) or sentence_with(AREA, doc_lines)
    site = chunk["site"]
    title = page_title(chunk.get("title"), site)
    name = near or institution(lines) or title or institution(doc_lines) or site
    if GENERAL.fullmatch(name) and not near:  # a site-wide page title: the page's own address names it better
        name = slug_name(chunk["url"]) or name
    fallback_area = ": ".join(x for x in (chunk.get("category"), title) if x) or None
    phones, emails = list(dict.fromkeys(phones)), list(dict.fromkeys(e.lower() for e in emails))
    # What the page is about, for finding the card (not shown): its first sentence without a phone or e-mail.
    about = next((sentence.strip() for line in lines if line["line_id"] not in used
                  for sentence in re.split(r"(?<=[.!?])\s+", line["text"])[:1] if len(sentence) > 20), "")
    line_ids = list(dict.fromkeys(used + [x[1] for x in (address, hours) if x]))
    key = f"{site}|{'|'.join(sorted(phones))}|{'|'.join(sorted(emails))}"
    return {
        "contact_id": hashlib.sha1(key.encode()).hexdigest()[:16],
        "name": name,
        "area": area[0] if area else fallback_area,
        "phone": phones,
        "email": emails,
        "address": address[0] if address else None,
        "hours": hours[0] if hours else None,
        "url": chunk["url"],
        "site": site,
        "category": chunk.get("category"),
        "doc_id": chunk["doc_id"],
        "line_ids": line_ids,
        # The City Hall named on the phone's own line: not a site whose pages all carry its name in the title,
        # nor a department's phone under a sentence about the City Hall.
        "is_general": general,
        "about": about[:MAX_FIELD],
    }


CONTACT_PAGE = re.compile(r"contact|audien|despre|about|контакт", re.I)


def dedupe(cards: list[dict]) -> list[dict]:
    """The same phones and e-mails on many pages of a site (footer) are one card: the one from a contact page,
    else the shortest URL. A card named just "Primăria …" (the City Hall named in a nearby sentence, not next to the
    phone) takes the name of the department its site's cards name most often."""
    best: dict[str, dict] = {}
    for card in sorted(cards, key=lambda c: (not CONTACT_PAGE.search(c["url"]), len(c["url"]))):
        best.setdefault(card["contact_id"], card)
    owners: dict[str, Counter] = {}
    for card in best.values():
        if INSTITUTION_WORD.match(card["name"]) and not GENERAL.search(card["name"]):
            owners.setdefault(card["site"], Counter())[card["name"]] += 1
    for card in best.values():
        if GENERAL.fullmatch(card["name"]) and not card["is_general"] and card["site"] in owners:
            card["name"] = owners[card["site"]].most_common(1)[0][0]
    return list(best.values())
