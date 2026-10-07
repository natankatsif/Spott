"""Act type, number and date: from the act's own title block, its file name, then the link text.

Never from the body: acts cite other acts ("Codul … nr. 434/2023", "contractului nr. 41/26 din 20.05.2026"),
and those numbers and dates aren't the act's own. The title block may be OCR output, so when its number
disagrees with the file name (typed by a person), the file name wins.
"""

import re
from urllib.parse import unquote, urlsplit

DOC_TYPES = [
    ("decizie", r"decizi[ae]|решени[ея]"),
    ("dispozitie", r"dispozi[țt]i[ae]|распоряжени[ея]"),
    ("hotarare", r"hot[ăa]r[âa]re[a]?|постановлени[ея]"),
    ("regulament", r"regulament(?:ul)?|положени[ея]"),
    ("ordin", r"ordin(?:ul)?|приказ"),
    ("lege", r"lege[a]?|закон"),
    ("proces-verbal", r"proces(?:ul)?[- ]verbal|протокол"),
    ("anunt", r"anun[țt](?:ul)?|объявлени[ея]"),
]
TYPE_RES = [(name, re.compile(rf"\b(?:{pattern})\b", re.I)) for name, pattern in DOC_TYPES]

NUMBER = re.compile(
    r"(?:\bnr\.?|\bn\.(?=\s*\d)|№|\b(?:decizi[ae]|dispozi[țt]i[ae]|hot[ăa]r[âa]re[a]?|ordin(?:ul)?))[\s_.-]*"
    r"(?:nr\.?[\s_-]*)?(\d+(?:[/.-]\d+)*(?:\s*-\s*(?!din\b)[a-zA-Zа-яА-Я]{1,3}\b)?)",
    re.I,
)

MONTHS = {
    **{m: i for i, m in enumerate(
        ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie",
         "august", "septembrie", "octombrie", "noiembrie", "decembrie"], 1)},
    **{m: i for i, m in enumerate(
        ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
         "августа", "сентября", "октября", "ноября", "декабря"], 1)},
}
DATE_NUMERIC = re.compile(r"\b(\d{1,2})[./_-](\d{1,2})[./_-](\d{4})\b")
DATE_WORDS = re.compile(rf"\b(\d{{1,2}})\s+({'|'.join(MONTHS)})\s+(\d{{4}})\b", re.I)


def find_type(text: str) -> str | None:
    hits = [(m.start(), name) for name, rx in TYPE_RES if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def find_numbers(text: str) -> list[str]:
    return [re.sub(r"\s+", "", m.group(1)) for m in NUMBER.finditer(text)]


def find_number(text: str) -> str | None:
    return next(iter(find_numbers(text)), None)


def pick_number(links: str, head: str) -> str | None:
    """Number from the link, but written as in the document when the digits agree (1214 → 12/14)."""
    in_head = find_numbers(head)
    if not (in_link := find_number(links)):
        return next(iter(in_head), None)
    digits = re.sub(r"\D", "", in_link)
    return next((n for n in in_head if re.sub(r"\D", "", n) == digits), in_link)


def find_date(text: str) -> str | None:
    candidates = []
    # File names separate words with - and _ ("…-din-25-august-2026").
    for variant in (text, re.sub(r"[-_]+", " ", text)):
        if m := DATE_NUMERIC.search(variant):
            candidates.append((m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1))))
        if m := DATE_WORDS.search(variant):
            candidates.append((m.start(), int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))))
    for _, year, month, day in sorted(candidates):
        if 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def file_names(sources: list[dict]) -> str:
    names = (unquote(urlsplit(s["url"]).path.rsplit("/", 1)[-1]) for s in sources)
    return " | ".join(re.sub(r"\.\w+$", "", n) for n in names if n)


def anchor_texts(sources: list[dict]) -> str:
    return " | ".join(s["anchor_text"] for s in sources if s.get("anchor_text"))


def link_text(sources: list[dict]) -> str:
    """Anchor texts and file names of all links to the document, as one searchable string."""
    return " | ".join(p for p in (anchor_texts(sources), file_names(sources).replace("_", " ")) if p)


# Letterhead lines that open every act and say nothing about its content.
LETTERHEAD = re.compile(
    r"^(republica moldova|prim[aă]r|consiliul municipal|республика молдова|примар|муниципальный совет)", re.I)
# Subject line of an act: "Cu privire la ...", "privind ...", "О ...", "Об ...".
SUBJECT = re.compile(r"^(cu privire la|privind|despre|об|о)\s", re.I)
HEAD_BLOCKS = 12


def pick_title(blocks: list[dict], sources: list[dict]) -> str | None:
    head = blocks[:HEAD_BLOCKS]
    if subject := next((b["text"] for b in head if SUBJECT.match(b["text"])), None):
        return subject[:300]
    if heading := next((b["text"] for b in blocks if b["type"] == "heading"
                        and len(b["text"]) >= 8 and not LETTERHEAD.match(b["text"])), None):
        return heading
    if anchors := [s["anchor_text"] for s in sources if s.get("anchor_text")]:
        return max(anchors, key=len)
    return next((b["text"] for b in blocks if b["type"] == "heading"), None)


ACT_WORD = re.compile(rf"^\W*(?:{'|'.join(p for _, p in DOC_TYPES)})\b", re.I)
# The body of an act starts here; nothing below belongs to the title block.
BODY_START = re.compile(
    r"^(?:(?:cu privire la|privind|despre|об|о|având|avînd|în temeiul|in temeiul|în scopul|în conformitate|"
    r"în baza|prezentul|articolul|capitolul|на основании|в соответствии|статья|глава)\b|art\.|\d+\.\s)", re.I)
OWN_SHORT_LINE = re.compile(r"^\W*(nr\b|n\.|№|din\b|от\b)", re.I)
SUBJECT_INSIDE = re.compile(r"\b(cu privire la|privind|despre)\b", re.I)


def title_block(blocks: list[dict]) -> str:
    """The act's own heading lines: "DISPOZIȚIE nr. 373-d din 25 august 2026", "nr. 12/14", "din 28 iulie 2020".
    Stops at the subject line or the first body line; a heading merged with the subject is cut before it."""
    own = []
    for b in blocks[:HEAD_BLOCKS]:
        text = b["text"].strip()
        if BODY_START.match(text):
            break
        if ACT_WORD.match(text):
            own.append(SUBJECT_INSIDE.split(text)[0])
        elif len(text) <= 40 and OWN_SHORT_LINE.match(text):
            own.append(text)
    return "\n".join(own)


def extract(blocks: list[dict], sources: list[dict]) -> dict:
    headings = "\n".join(b["text"] for b in blocks[:HEAD_BLOCKS] if b["type"] == "heading")
    head, files, anchors = title_block(blocks), file_names(sources), anchor_texts(sources)
    # The act names its own type in a heading ("DECIZIE"); link labels on the sites are sometimes wrong.
    # Body text is not used: it mentions other acts ("Legea nr. 436/2006") and plain words ("решение").
    doc_type = find_type(headings) or find_type(head) or find_type(anchors) or find_type(files)
    number = date = None
    if doc_type:  # outside acts, "nr." and dates are addresses, school numbers, referenced laws
        number = pick_number(files, head) or find_number(anchors)
        date = find_date(head) or find_date(files) or find_date(anchors)
    return {
        "title": pick_title(blocks, sources),
        "doc_type": doc_type,
        "number": number,
        "date": date,
        "effective_date": date,
    }
