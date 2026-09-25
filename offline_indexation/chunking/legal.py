"""Legal hierarchy parser for Moldovan municipal acts (RO and RU).

Tracks hierarchy levels:
  0: Anexa / Приложение
  1: Capitol / Глава
  2: Secțiune / Раздел
  3: Articol / Статья
  4: Punct / Пункт (pct. 12, 5.2, 12.)
  5: Alineat / Часть (alin. (2), (3), часть 2)
  6: Litera / Подпункт (lit. a), a), подпункт b))

Enforces hierarchical stack popping and prevents false positives (e.g. citations in text, dates).
"""

import re
from enum import IntEnum


class LegalLevel(IntEnum):
    ANEXA = 0
    CAPITOL = 1
    SECTIUNE = 2
    ARTICOL = 3
    PUNCT = 4
    ALINEAT = 5
    LITERA = 6


DATE_PREFIX = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{4}\b")

# Regex rules in order of precedence:
ANEXA_RE = re.compile(
    r"^\s*(anexa|приложение)\b(?:\s+(?:nr\.?|n\.|№))?\s*([0-9]+[a-zA-Zа-яА-Я0-9/.-]*)?",
    re.IGNORECASE,
)
CAPITOL_RE = re.compile(
    r"^\s*(capitolul|глава)\s+([IVXLCDM]+|[0-9]+)\b",
    re.IGNORECASE,
)
SECTIUNE_RE = re.compile(
    r"^\s*(sec[țt]iunea|раздел)\s+(?:(?:nr\.?|№)\s*)?([IVXLCDM]+|[0-9]+)\b",
    re.IGNORECASE,
)
# Note: negative lookahead avoids references like "Art. 14 din Legea..." or "Ст. 14 Закона..."
ARTICOL_RE = re.compile(
    r"^\s*(articolul|art\.|статья|ст\.)\s*([0-9]+(?:[-/][0-9]+)*)(?!\s+(?:din\s+(?:legea|codul|hot[ăa]r|decizi|ordin)|al\s+legii|закона|кодекса|постановления|решения))\b",
    re.IGNORECASE,
)
PCT_RE = re.compile(
    r"^\s*(?:(punctul|pct\.|пункт|п\.)\s*([0-9]+(?:\.[0-9]+)*)|([0-9]+(?:\.[0-9]+)+)\.?\s+|([0-9]+)\.\s+(?!\d{1,2}\.))",
    re.IGNORECASE,
)
ALIN_RE = re.compile(
    r"^\s*(?:(alineatul|alin\.|часть|ч\.)\s*\(?([0-9]+)\)?|\(([0-9]+)\)\s+)",
    re.IGNORECASE,
)
LIT_RE = re.compile(
    r"^\s*(?:(litera|lit\.|подпункт|пп\.)\s*([a-zA-Zа-яА-Я])\)|([a-zA-Zа-яА-Я])\)\s+)",
    re.IGNORECASE,
)


def match_legal_item(text: str, marker: str = "") -> tuple[int, str] | None:
    """Inspects text (and optional marker) to find if it begins with a legal structural item."""
    candidate = text.strip()
    if not candidate:
        return None

    # Dates like 12.03.2023 must never match pct
    if DATE_PREFIX.match(candidate):
        return None

    # Check marker first if provided
    if marker and marker.strip():
        m_strip = marker.strip()
        # e.g. "12." or "5.2" or "a)"
        m_item = match_legal_item(m_strip)
        if m_item:
            return m_item

    # 0: Anexa / Приложение
    m = ANEXA_RE.match(candidate)
    if m:
        word, num = m.group(1).lower(), m.group(2)
        if "anex" in word:
            return LegalLevel.ANEXA, f"Anexa nr. {num}" if num else "Anexa"
        return LegalLevel.ANEXA, f"Приложение № {num}" if num else "Приложение"

    # 1: Capitolul / Глава
    m = CAPITOL_RE.match(candidate)
    if m:
        word, num = m.group(1).lower(), m.group(2)
        return LegalLevel.CAPITOL, f"Capitolul {num}" if "cap" in word else f"Глава {num}"

    # 2: Sectiunea / Раздел
    m = SECTIUNE_RE.match(candidate)
    if m:
        word, num = m.group(1).lower(), m.group(2)
        return LegalLevel.SECTIUNE, f"Secțiunea {num}" if "sec" in word else f"Раздел {num}"

    # 3: Articolul / Art. / Статья / Ст.
    m = ARTICOL_RE.match(candidate)
    if m:
        word, num = m.group(1).lower(), m.group(2)
        if "art" in word:
            return LegalLevel.ARTICOL, f"Articolul {num}" if "articol" in word else f"Art. {num}"
        return LegalLevel.ARTICOL, f"Статья {num}" if "статья" in word else f"Ст. {num}"

    # 4: punct / pct. / пункт / п. / 12. / 5.2.
    m = PCT_RE.match(candidate)
    if m:
        word = (m.group(1) or "").lower()
        num = m.group(2) or m.group(3) or m.group(4)
        if "пункт" in word or "п." in word:
            return LegalLevel.PUNCT, f"п. {num}"
        return LegalLevel.PUNCT, f"pct. {num}"

    # 5: alineatul / alin. / parte / ч. / (1)
    m = ALIN_RE.match(candidate)
    if m:
        word = (m.group(1) or "").lower()
        num = m.group(2) or m.group(3)
        if "част" in word or "ч." in word:
            return LegalLevel.ALINEAT, f"ч. ({num})"
        return LegalLevel.ALINEAT, f"alin. ({num})"

    # 6: litera / lit. / подпункт / пп. / a)
    m = LIT_RE.match(candidate)
    if m:
        word = (m.group(1) or "").lower()
        letter = m.group(2) or m.group(3)
        if "под" in word or "пп." in word:
            return LegalLevel.LITERA, f"подп. {letter})"
        return LegalLevel.LITERA, f"lit. {letter})"

    return None


def is_act_or_has_major_legal(meta: dict | None = None, blocks: list[dict] | None = None) -> bool:
    """Checks if a document is an official act or contains major legal structural markers (Anexa, Capitol, Secțiune, Articol)."""
    if meta and meta.get("doc_type"):
        return True
    if blocks:
        for b in blocks:
            text = b.get("text", "")
            marker = b.get("marker", "")
            match = match_legal_item(text, marker)
            if match and match[0] <= LegalLevel.ARTICOL:
                return True
    return False


class LegalHierarchyTracker:
    """Maintains a stack of legal hierarchy levels for a document."""

    def __init__(self, allow_sub_articles: bool = True):
        self.stack: list[tuple[int, str]] = []
        self.allow_sub_articles = allow_sub_articles

    def reset(self) -> None:
        self.stack.clear()

    def process_block(self, block: dict) -> list[str]:
        b_type = block.get("type")
        is_heading = b_type in ("heading", "title", "section_header")
        if is_heading:
            # New heading resets PUNCT and deeper levels
            while self.stack and self.stack[-1][0] >= LegalLevel.PUNCT:
                self.stack.pop()

        text = block.get("text", "")
        marker = block.get("marker", "")

        match = match_legal_item(text, marker)
        if match:
            level, label = match
            if level < LegalLevel.PUNCT or self.allow_sub_articles:
                # Pop deeper or equal levels
                while self.stack and self.stack[-1][0] >= level:
                    self.stack.pop()
                self.stack.append((level, label))

        path = [label for _, label in self.stack]
        block["legal_path"] = path
        return path
