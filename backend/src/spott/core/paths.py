"""Where Spott keeps its files.

DATA_DIR holds everything generated: crawl output, downloaded and parsed files, registry.sqlite, logs, index dumps.
Only data/sources/sites.toml is versioned. In Docker it is the `oidata` volume (SPOTT_DATA_DIR=/app/data).
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]  # backend/ (this file is backend/src/spott/core/paths.py)
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("SPOTT_DATA_DIR") or REPO_ROOT / "data")
SITES_TOML = DATA_DIR / "sources" / "sites.toml"
REGISTRY = DATA_DIR / "registry.sqlite"
EVAL_DIR = BACKEND_DIR / "eval"
