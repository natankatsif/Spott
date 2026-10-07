"""Facts about a retrieved chunk (a row of the chunks table): its place in the document, whether it is an act, its date
or the latest date it mentions, its title in citations, whether it lists people with their roles."""

import re
from datetime import UTC, datetime

ROLE = re.compile(r"\b(membr[ui]\w*|președint\w*|vicepreședint\w*|secretar\w*|coordonator\w*|член\w*|"
                  r"председател\w*|секретар\w*|заместител\w*)", re.I)
NUMBERED_NAME = re.compile(r"^\s*\d+[.)]\s+[A-ZĂÂÎȘȚА-ЯЁ][\w-]+\s+[A-ZĂÂÎȘȚА-ЯЁ][\w-]+\s*[,–—-]")
ACT_NAMES = {
    "decizie": "Decizia", "dispozitie": "Dispoziția", "hotarare": "Hotărârea", "regulament": "Regulamentul",
    "ordin": "Ordinul", "lege": "Legea", "proces-verbal": "Procesul-verbal", "anunt": "Anunțul",
}


def position(chunk: dict) -> int | None:
    blocks = chunk.get("block_ids")
    return blocks[0] if blocks else None


def ro_date(iso: str | None) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m[3]}.{m[2]}.{m[1]}" if m else iso


def document_title(chunk: dict) -> str:
    """'Decizia nr. 12/14 din 28.07.2020 cu privire la …' for acts, the document title otherwise."""
    if chunk.get("doc_type") and chunk.get("number"):
        label = f"{ACT_NAMES.get(chunk['doc_type'], chunk['doc_type'].capitalize())} nr. {chunk['number']}"
        if chunk.get("date"):
            label += f" din {ro_date(chunk['date'])}"
        title = (chunk.get("title") or "").strip()
        if re.match(r"(cu privire|privind|despre)\b", title, re.I):
            label += " " + title[0].lower() + title[1:]
        return label if len(label) <= 160 else label[:157].rstrip() + "…"
    return chunk.get("title") or chunk.get("citation_label") or chunk.get("site") or chunk.get("url") or ""


def quote_lang(chunk: dict) -> str:
    lang = (chunk.get("lang") or "ro")[:2]
    return lang if lang in ("ro", "ru", "en", "uk") else "ro"


def is_act(chunk: dict) -> bool:
    return chunk.get("doc_type") not in (None, "page") and bool(chunk.get("number"))


MONTHS = {m: i for names in (
    ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie",
     "noiembrie", "decembrie"],
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
     "декабря"]) for i, m in enumerate(names, 1)}
DATE_NUMERIC = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
DATE_WORDS = re.compile(rf"\b(\d{{1,2}})\s+({'|'.join(MONTHS)})\s+(\d{{4}})\b", re.I)


def latest_date(texts: list[str], today: str | None = None) -> str | None:
    """Latest full date mentioned in the texts, not after today: an undated document is at least that recent."""
    today = today or datetime.now(UTC).date().isoformat()
    found = []
    for text in texts:
        for d, m, y in DATE_NUMERIC.findall(text):
            found.append((int(y), int(m), int(d)))
        for d, m, y in DATE_WORDS.findall(text):
            found.append((int(y), MONTHS[m.lower()], int(d)))
    dates = [f"{y:04d}-{m:02d}-{d:02d}" for y, m, d in found if y >= 1990 and 1 <= m <= 12 and 1 <= d <= 31]
    return max((d for d in dates if d <= today), default=None)


def recency(chunk: dict) -> str | None:
    return chunk.get("date") or chunk.get("mentions_until")


def newest_first(chunks: list[dict]) -> list[dict]:
    """Newest first by the act's date, or for undated documents the latest date they mention;
    documents with neither keep their original (relevance) order at the end."""
    dated = sorted((c for c in chunks if recency(c)), key=recency, reverse=True)
    return dated + [c for c in chunks if not recency(c)]


def distinct(chunks: list[dict]) -> list[dict]:
    """One chunk per text: copies of a document published twice ("…-(1).pdf") repeat the same chunks."""
    seen, out = set(), []
    for c in chunks:
        key = c.get("content_hash") or c["chunk_id"]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def is_roster(chunk: dict) -> bool:
    """A list of people with their roles: table rows or numbered "Name Surname – role" lines."""
    rows = [t for t in (chunk.get("text") or "").split("\n")
            if ROLE.search(t) and (t.count("|") >= 2 or NUMBERED_NAME.match(t))]
    return len(rows) >= 3
