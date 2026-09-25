"""Text normalization for RO/RU documents."""

import re
import unicodedata

# Romanian: cedilla forms (legacy fonts, OCR) → comma-below forms.
CHAR_FIXES = str.maketrans({
    "ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț",
    " ": " ", " ": " ", " ": " ",   # non-breaking / thin spaces
    "­": None,                                # soft hyphen
    "ﬁ": "fi", "ﬂ": "fl",
})

# Latin and Cyrillic letters that look the same; OCR and copy-paste mix them inside one word.
LAT_TO_CYR = dict(zip("aceopxyABCEHKMOPTX", "асеорхуАВСЕНКМОРТХ"))
CYR_TO_LAT = {v: k for k, v in LAT_TO_CYR.items()}

WORD = re.compile(r"\w+")
SPACES = re.compile(r"[ \t]+")


def script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    if name.startswith("CYRILLIC"):
        return "cyr"
    if name.startswith("LATIN"):
        return "lat"
    return None


def _fix_mixed_word(m: re.Match) -> str:
    word = m.group()
    scripts = [script(ch) for ch in word]
    cyr, lat = scripts.count("cyr"), scripts.count("lat")
    if not cyr or not lat or cyr == lat:
        return word
    table = LAT_TO_CYR if cyr > lat else CYR_TO_LAT
    minority = "lat" if cyr > lat else "cyr"
    if all(ch in table for ch, s in zip(word, scripts) if s == minority):
        return "".join(table.get(ch, ch) if s == minority else ch for ch, s in zip(word, scripts))
    return word


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(CHAR_FIXES)
    text = WORD.sub(_fix_mixed_word, text)
    return "\n".join(SPACES.sub(" ", line).strip() for line in text.split("\n")).strip()


UK_LETTERS = set("іїєґІЇЄҐ")
RO_WORDS = {"și", "si", "în", "de", "la", "cu", "pentru", "din", "privind", "care", "este", "al", "ale", "sau"}
EN_WORDS = {"the", "and", "of", "to", "for", "with", "is", "are", "on", "your", "you", "by", "from", "this"}


def detect_lang(text: str) -> str | None:
    """ro / ru / uk / en by script, Ukrainian-only letters and function words; None if no letters."""
    cyr = lat = uk = 0
    for ch in text:
        s = script(ch)
        cyr += s == "cyr"
        lat += s == "lat"
        uk += ch in UK_LETTERS
    if not cyr and not lat:
        return None
    if cyr > lat:
        return "uk" if uk > 0.01 * cyr else "ru"
    words = [w.lower() for w in WORD.findall(text)]
    en = sum(w in EN_WORDS for w in words)
    ro = sum(w in RO_WORDS for w in words)
    return "en" if en > ro else "ro"
