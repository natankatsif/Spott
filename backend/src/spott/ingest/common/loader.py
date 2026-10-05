"""Loads active document payloads from registry and parsed files.

Only active (non-removed, parsed) documents are loaded.
Old versions left in data/parsed/<sha>.json are ignored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from spott.ingest.common.registry import Registry

log = logging.getLogger(__name__)


def load_active_documents(
    data_dir: Path,
    *,
    files_only: bool = False,
    pages_only: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Loads only the current, active version of each document from registry.
    If registry does not exist, falls back to globbing data/parsed/*.json.
    """
    registry_path = data_dir / "registry.sqlite"
    docs: list[dict] = []

    if registry_path.exists():
        registry = Registry(registry_path)
        active_files = [] if pages_only else registry.active_file_documents()
        active_pages = [] if files_only else registry.active_page_documents()
        registry.close()

        parsed_dir = data_dir / "parsed"

        # 1. Official files (PDF/DOCX)
        for fdoc in active_files:
            p_json = parsed_dir / f"{fdoc['sha256']}.json"
            if p_json.exists():
                try:
                    doc = json.loads(p_json.read_text(encoding="utf-8"))
                    doc["doc_id"] = fdoc["doc_id"]
                    doc["primary_url"] = fdoc["primary_url"]
                    doc["url_key"] = fdoc["url_key"]
                    doc["version"] = fdoc.get("version", 1)
                    doc["previous_sha256"] = fdoc.get("previous_sha256")
                    doc["updated_at"] = fdoc.get("updated_at")
                    doc["sources"] = fdoc.get("sources", doc.get("sources", []))
                    docs.append(doc)
                except Exception as e:
                    log.warning("Failed loading file %s: %s", p_json.name, e)

        # 2. HTML Pages
        pages_dir = parsed_dir / "pages"
        if pages_dir.exists():
            for pdoc in active_pages:
                url_hash = hashlib.sha1(pdoc["url_key"].encode("utf-8")).hexdigest()[:20]
                p_json = pages_dir / f"{url_hash}.json"
                if p_json.exists():
                    try:
                        doc = json.loads(p_json.read_text(encoding="utf-8"))
                        doc["doc_id"] = pdoc["doc_id"]
                        doc["url_key"] = pdoc["url_key"]
                        doc["version"] = pdoc.get("version", 1)
                        doc["updated_at"] = pdoc.get("fetched_at")
                        docs.append(doc)
                    except Exception as e:
                        log.warning("Failed loading page %s: %s", p_json.name, e)
    else:
        # Fallback for standalone tests without registry
        parsed_dir = data_dir / "parsed"
        pages_dir = parsed_dir / "pages"

        if not pages_only and parsed_dir.exists():
            for p in sorted(parsed_dir.glob("*.json")):
                if not p.name.endswith(".docling.json"):
                    try:
                        docs.append(json.loads(p.read_text(encoding="utf-8")))
                    except Exception as e:
                        log.warning("Failed loading %s: %s", p.name, e)

        if not files_only and pages_dir.exists():
            for p in sorted(pages_dir.glob("*.json")):
                try:
                    docs.append(json.loads(p.read_text(encoding="utf-8")))
                except Exception as e:
                    log.warning("Failed loading page %s: %s", p.name, e)

    if limit is not None and limit > 0:
        docs = docs[:limit]

    return docs
