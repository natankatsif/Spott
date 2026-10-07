"""Helpers for generating verifiable source links and Chrome text fragments."""

from __future__ import annotations

import urllib.parse


def make_deep_link(url: str, text: str, page: int | None = None) -> str:
    """Builds deep link for PDF or HTML page.

    - PDF: {url}#page={page} (opens specified page in browser)
    - HTML: {url}#:~:text={quote(first_8_words)} (Chrome scroll-to-text fragment)
    """
    if not url:
        return ""

    parsed = urllib.parse.urlparse(url)
    is_pdf = parsed.path.lower().endswith(".pdf") or (
        page is not None and page > 0 and not parsed.path.lower().endswith((".html", ".htm", "/"))
    )

    if is_pdf:
        p = page if page is not None and page > 0 else 1
        return f"{url}#page={p}"

    # HTML page: extract first ~8 words for Chrome text fragment
    words = [w for w in text.split() if w]
    first_words = " ".join(words[:8])
    if first_words:
        encoded = urllib.parse.quote(first_words)
        return f"{url}#:~:text={encoded}"
    return url
