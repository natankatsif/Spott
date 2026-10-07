"""Our stored copies of downloaded documents (<data>/raw/<sha[:2]>/<sha>.<ext>, spott.core.paths),
served to the PDF viewer because city hall sites don't send CORS headers."""

import re
from pathlib import Path

from spott.core.paths import DATA_DIR

SHA = re.compile(r"^[0-9a-f]{64}$")


def raw_pdf(sha256: str | None) -> Path | None:
    """The stored PDF with this content, or None (web page, other format, or not on this machine)."""
    if not sha256 or not SHA.match(sha256):
        return None
    path = DATA_DIR / "raw" / sha256[:2] / f"{sha256}.pdf"
    return path if path.is_file() else None
