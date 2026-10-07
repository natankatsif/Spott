"""The languages questions are answered in: their names in the model prompts, and which one a question is in."""

import re

LANGUAGE_NAMES = {"ro": "Romanian", "ru": "Russian", "en": "English"}
# Latin-script questions: Romanian or English, by the Romanian letters and each language's common words.
RO_LETTERS = re.compile(r"[ăâîșşțţ]", re.I)
RO_WORDS = {"este", "care", "cum", "unde", "cine", "cand", "când", "pentru", "si", "și", "din", "sunt", "pot", "trebuie",
            "primaria", "primăria", "cu", "pe", "ale", "al", "la", "de", "ce", "un", "o", "sa", "să", "nu", "mai",
            "orasul", "chisinau", "acte", "cerere", "taxa", "program"}
EN_WORDS = {"the", "is", "are", "what", "how", "where", "who", "when", "which", "can", "do", "does", "my", "to", "of",
            "for", "and", "with", "need", "get", "about", "hall", "city", "have", "there", "should", "much", "cost",
            "i", "you", "your", "an", "it", "this", "that", "please", "hello", "thanks", "open", "hours"}


def detect_lang(text: str, fallback: str | None = None) -> str:
    """Language of the question: Russian (Cyrillic), Romanian or English; the UI language decides when there are
    too few letters ("PUG 2021?") or the words don't tell."""
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isalpha()) - cyr
    if cyr + lat < 5:
        return fallback or ("ru" if cyr > lat else "ro")
    if cyr > lat:
        return "ru"
    if RO_LETTERS.search(text):
        return "ro"
    words = re.findall(r"[a-z]+", text.lower())
    ro, en = sum(w in RO_WORDS for w in words), sum(w in EN_WORDS for w in words)
    if en > ro:
        return "en"
    if ro > en:
        return "ro"
    return fallback if fallback in ("ro", "en") else "ro"
