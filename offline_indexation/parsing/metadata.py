"""Act type, number and date from the document head and from the links pointing to it.

Links (anchor text, file name) are typed by a person, the document head may be OCR output,
so number and date prefer the link and fall back to the text.
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
    if m := DATE_NUMERIC.search(text):
        candidates.append((m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1))))
    if m := DATE_WORDS.search(text):
        candidates.append((m.start(), int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))))
    for _, year, month, day in sorted(candidates):
        if 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def link_text(sources: list[dict]) -> str:
    """Anchor texts and file names of all links to the document, as one searchable string."""
    parts = []
    for s in sources:
        parts.append(s.get("anchor_text") or "")
        name = unquote(urlsplit(s["url"]).path.rsplit("/", 1)[-1])
        parts.append(re.sub(r"\.\w+$", "", name).replace("_", " "))
    return " | ".join(p for p in parts if p)


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


def extract(blocks: list[dict], sources: list[dict]) -> dict:
    head = "\n".join(b["text"] for b in blocks[:HEAD_BLOCKS])
    headings = "\n".join(b["text"] for b in blocks[:HEAD_BLOCKS] if b["type"] == "heading")
    links = link_text(sources)
    # The act names its own type in a heading ("DECIZIE"); link labels on the sites are sometimes wrong.
    # Body text is not used: it mentions other acts ("Legea nr. 436/2006") and plain words ("решение").
    doc_type = find_type(headings) or find_type(links)
    # Outside acts, "nr." and dates in the text are addresses, school numbers, referenced laws.
    return {
        "title": pick_title(blocks, sources),
        "doc_type": doc_type,
        "number": pick_number(links, head) if doc_type else None,
        "date": find_date(links) or (find_date(head) if doc_type else None),
    }
