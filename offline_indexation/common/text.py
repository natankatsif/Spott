"""Text processing utilities: table markdown formatting and contact detection."""

import re

PHONE_RE = re.compile(r"(?:(?:\+373|0)\s*\(?\d{2,3}\)?[\s.-]*\d{2,3}[\s.-]*\d{2,4})")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def has_contacts(text: str) -> bool:
    """Checks if text contains phone numbers or email addresses."""
    return bool(PHONE_RE.search(text) or EMAIL_RE.search(text))


check_contacts = has_contacts


def format_table_markdown(header: list[str], rows: list[list[str]]) -> str:
    """Formats table header and rows into GitHub-flavored Markdown table."""
    cols = max(len(header), max((len(r) for r in rows), default=0))
    if cols == 0:
        return ""
    h = header + [""] * (cols - len(header))
    lines = [
        "| " + " | ".join(h) + " |",
        "| " + " | ".join(["---"] * cols) + " |",
    ]
    for r in rows:
        padded = r + [""] * (cols - len(r))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)
