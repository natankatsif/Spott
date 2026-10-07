"""References between acts in the text: "Se operează modificări în textul deciziei … nr. 4/1 din 05.03.2020".

A reference is an act type word, optional words, optional "nr."/"№", a number and an optional date.
Its relation comes from the sentence it stands in:
    amends   "se modifică", "se operează modificări", "se completează", "вносятся изменения"
    repeals  "se abrogă", "își încetează activitatea", "își pierde valabilitatea", "утрачивает силу"
    refers   anything else, and always in a preamble ("Având în vedere…", "În temeiul…"): there the act
             only lists what it relies on, even when the listed act itself amended another.
"""

import re
from dataclasses import dataclass

from spott.ingest.parsing.metadata import find_date

TYPES = [
    ("decizie", r"decizi\w*"),
    ("dispozitie", r"dispozi[țţt]i\w*"),
    ("hotarare", r"hot[ăa]r[âîa]r\w*"),
    ("ordin", r"ordin(?:ul|ului)?"),
    ("decizie", r"решени\w*"),
    ("dispozitie", r"распоряжени\w*"),
    ("hotarare", r"постановлени\w*"),
]
TYPE_WORD = "|".join(p for _, p in TYPES)
DATE = r"\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}\s+[a-zA-Zăâîșțа-яё]+\s+\d{4}"
REFERENCE = re.compile(
    rf"\b(?P<type>{TYPE_WORD})\s+"
    rf"(?P<gap>[^\n\d.,;:„\"«]{{0,90}}?)"  # "Consiliului municipal Chișinău", "Primarului General"
    rf"(?:nr\.?|№)?\s*"
    rf"(?P<number>\d+(?:[/-]\d+)*(?:\s?-\s?[a-zа-я]{{1,2}}\b)?)"
    rf"(?:\s*(?:din|от)\s+(?P<date>{DATE}))?",
    re.I,
)
PREAMBLE = re.compile(
    r"^\W*(având|avînd|luând|luînd|în temeiul|in temeiul|în conformitate|în baza|în scopul|conform|"
    r"учитывая|в соответствии|на основании|руководствуясь)", re.I)
REPEALS = re.compile(
    r"se abrog|abrogat|își încetează activitatea|isi inceteaza activitatea|își pierde valabilitatea|"
    r"își pierd valabilitatea|утрачива\w* силу|утратившим силу|отменить|отменяется", re.I)
AMENDS = re.compile(
    r"se modific|se operează modificări|operarea unor modificări|se completează|modificări(?:le)? în|"
    r"внести изменени|вносятся изменени", re.I)
# Sentence ends: before a numbered point ("6. Grupul…") or a capitalised word after . or ;
# — not after "nr." followed by the number on the next line — and at a line opening a preamble
# or a point, since titles above them often have no final period.
SENTENCE_BREAK = re.compile(
    r"(?<=[.;])\s+(?=\d+\.\s|[A-ZĂÂÎȘȚА-ЯЁ][a-zăâîșțа-яё])"
    r"|\n(?=\d+\.\s|(?i:\W*(?:având|avînd|luând|luînd|în temeiul|in temeiul|în conformitate|în baza|"
    r"în scopul|учитывая|на основании|руководствуясь)\b))")


@dataclass
class Reference:
    start: int
    end: int
    text: str
    doc_type: str
    number: str
    date: str | None
    relation: str


def type_of(word: str) -> str:
    return next(name for name, pattern in TYPES if re.fullmatch(pattern, word, re.I))


def normalize_number(number: str) -> str:
    return re.sub(r"\s+", "", number).lower()


def sentence_around(text: str, pos: int) -> str:
    starts = [0] + [m.end() for m in SENTENCE_BREAK.finditer(text, 0, pos)]
    start = starts[-1]
    end_match = SENTENCE_BREAK.search(text, pos)
    return text[start: end_match.start() if end_match else len(text)]


def relation_of(sentence: str) -> str:
    if PREAMBLE.match(sentence):
        return "refers"
    if REPEALS.search(sentence):
        return "repeals"
    if AMENDS.search(sentence):
        return "amends"
    return "refers"


def find_references(text: str) -> list[Reference]:
    text = re.sub(r"(\d-[a-z])din\b", r"\1 din", text)  # OCR: "366-ddin"
    refs = []
    for m in REFERENCE.finditer(text):
        if m["gap"].strip().lower().endswith(("din", "от")):  # "hotărârea din 2020": a date, not a number
            continue
        sentence = sentence_around(text, m.start())
        refs.append(Reference(
            start=m.start(),
            end=m.end(),
            text=" ".join(m.group(0).split()),
            doc_type=type_of(m["type"]),
            number=normalize_number(m["number"]),
            date=find_date(m["date"]) if m["date"] else None,
            relation=relation_of(sentence),
        ))
    return refs
