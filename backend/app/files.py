"""Our stored copies of downloaded documents (offline_indexation/data/raw/<sha[:2]>/<sha>.<ext>),
served to the PDF viewer because city hall sites don't send CORS headers."""

import os
import re
from pathlib import Path

DATA_DIR = Path(os.getenv("OFFLINE_DATA_DIR") or Path(__file__).resolve().parents[2] / "offline_indexation" / "data")
SHA_DOC_ID = re.compile(r"^file:([0-9a-f]{64})$")  # doc_id format before documents were keyed by URL
SHA = re.compile(r"^[0-9a-f]{64}$")


def raw_pdf(doc_id: str, sha256: str | None) -> Path | None:
    """The stored PDF of a document, or None (web page, other format, or not on this machine)."""
    sha = sha256 or (m.group(1) if (m := SHA_DOC_ID.match(doc_id)) else None)
    if not sha or not SHA.match(sha):
        return None
    path = DATA_DIR / "raw" / sha[:2] / f"{sha}.pdf"
    return path if path.is_file() else None
