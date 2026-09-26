"""Cross-platform file names for data/ (Windows forbids <>:"/\\|?* and long paths)."""

import hashlib
import re

MAX_NAME_LEN = 120
_FORBIDDEN = re.compile(r'[<>:"/\\|?*&\x00-\x1f]')


def safe_filename(name: str, max_len: int = MAX_NAME_LEN) -> str:
    """Maps any id (URL key, doc_id) to a file name valid on Windows, macOS and Linux.

    Forbidden characters become "_", trailing dots/spaces are stripped, and names longer
    than max_len are cut and suffixed with a short hash of the original, so distinct ids
    stay distinct.
    """
    cleaned = _FORBIDDEN.sub("_", name).rstrip(" .")
    if len(cleaned) <= max_len and cleaned:
        return cleaned
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[: max_len - len(digest) - 1].rstrip(' .')}_{digest}"
