"""The code's check of the model's claims: the numbers of a sentence must be in the lines it cites."""

import re
import unicodedata

_THOUSANDS = re.compile(r"(?<=\d)[\s .](?=\d{3}\b)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    """Numbers in a text, comparable across formats: '8 000' = '8000', '01.04' ~ '1', '0,02' = '0.02'."""
    found = set()
    for n in _NUMBER.findall(_THOUSANDS.sub("", text)):
        n = n.replace(",", ".")
        found.add(n.lstrip("0") or "0")
        found.update(part.lstrip("0") or "0" for part in n.split("."))
    return found


def copied_from(value: str, evidence: list[str]) -> bool:
    """An address or opening hours copied from the lines: its numbers are there, and so are its words (a value
    without numbers, "bd. Ștefan cel Mare", is checked by its words alone)."""
    def words(text: str) -> set[str]:
        plain = unicodedata.normalize("NFKD", text.casefold())
        plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
        return {w for w in re.findall(r"\w+", plain) if len(w) >= 3 and not w.isdigit()}

    have = set().union(*(words(t) for t in evidence)) if evidence else set()
    return numbers_backed(value, evidence) and words(value) <= have


def numbers_backed(claim: str, evidence: list[str]) -> bool:
    """Every number in a claim must appear in its quotes or in the cited documents' labels."""
    wanted = {n.replace(",", ".").lstrip("0") or "0" for n in _NUMBER.findall(_THOUSANDS.sub("", claim))}
    return wanted <= set().union(*(numbers(e) for e in evidence)) if wanted else True
