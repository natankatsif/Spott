"""Document tree and agent tools (search, grep, toc, open) with OpenAI tool schemas."""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import (
    MAX_TOOL_GREP_CHARS,
    MAX_TOOL_OPEN_CHARS,
    MAX_TOOL_SEARCH_CHARS,
    MAX_TOOL_TOC_CHARS,
)
from .links import make_deep_link
from .pipeline import acquire_conn, retrieve

log = logging.getLogger("retrieval.tools")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search document corpus using hybrid dense vector + FTS search over chunks and lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "User question or search terms",
                    },
                    "lang": {
                        "type": "string",
                        "enum": ["ro", "ru"],
                        "description": "Optional language filter ('ro' or 'ru')",
                    },
                    "site": {
                        "type": "string",
                        "description": "Optional site domain filter (e.g. 'chisinau.md', 'dgaurf.md')",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of top chunks to return (default: 8)",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Exact text pattern matching in lines table via PostgreSQL trigram ILIKE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Literal text pattern to match (case-insensitive, special characters safely escaped)",
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Optional document ID to restrict search to",
                    },
                    "site": {
                        "type": "string",
                        "description": "Optional site domain filter",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toc",
            "description": "Get table of contents (document tree nodes ordered by document position).",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "Document ID to fetch the table of contents for",
                    },
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open",
            "description": "Open and read verbatim lines of a document node or chunk in document order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "Document ID containing the lines",
                    },
                    "node_id": {
                        "type": "string",
                        "description": "Optional node/chunk identifier to read",
                    },
                    "chunk_id": {
                        "type": "string",
                        "description": "Optional chunk ID to read (alternative to node_id)",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to return (default: 60)",
                        "default": 60,
                    },
                },
                "required": ["doc_id"],
            },
        },
    },
]


def escape_like_pattern(pattern: str) -> str:
    """Escapes SQL LIKE / ILIKE special wildcard characters (%, _, and backslash)."""
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def enforce_char_limit(data: dict[str, Any], max_chars: int, list_key: str) -> dict[str, Any]:
    """Ensures JSON-serialized dict does not exceed max_chars by trimming list_key items."""
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw) <= max_chars:
        return data

    items: list[Any] = data.get(list_key, [])
    while items and len(json.dumps(data, ensure_ascii=False)) > max_chars:
        items.pop()
    data["truncated"] = True
    return data


def build_toc_nodes(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Builds an ordered document tree / table of contents from chunk dicts."""
    nodes = []
    for c in chunks:
        sec = c.get("section") or []
        lp = c.get("legal_path") or []
        path_parts = list(sec) + list(lp)
        if path_parts:
            title = " › ".join(path_parts)
        else:
            title = c.get("citation_label") or c.get("title") or f"Section {c.get('ord', 0)}"

        nodes.append(
            {
                "node_id": c["chunk_id"],
                "chunk_id": c["chunk_id"],
                "title": title,
                "section": sec,
                "legal_path": lp,
                "ord": c.get("ord", 0),
                "line_count": c.get("line_count", 0),
            }
        )
    nodes.sort(key=lambda n: (n["ord"], n["chunk_id"]))
    return nodes


def search_tool(
    pool: ConnectionPool | psycopg.Connection,
    query: str,
    *,
    lang: str | None = None,
    site: str | None = None,
    k: int = 8,
) -> dict[str, Any]:
    """Tool: search corpus via hybrid retrieval pipeline."""
    result = retrieve(pool, query, lang=lang, site=site, k=k, rerank=False)
    items = []
    for c in result.items:
        items.append(
            {
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "title": c.get("title"),
                "citation_label": c.get("citation_label"),
                "url": c.get("url"),
                "found_on": c.get("found_on"),
                "score": round(float(c.get("score", 0.0)), 4),
                "matched_lines": c.get("matched_lines", []),
            }
        )

    out: dict[str, Any] = {
        "items": items,
        "total": len(items),
        "timings_ms": result.timings_ms,
    }
    return enforce_char_limit(out, MAX_TOOL_SEARCH_CHARS, "items")


def grep_tool(
    pool: ConnectionPool | psycopg.Connection,
    pattern: str,
    *,
    doc_id: str | None = None,
    site: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Tool: exact text substring match in lines table via PostgreSQL trigram ILIKE."""
    escaped = escape_like_pattern(pattern)
    sql_pattern = f"%{escaped}%"

    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT l.line_id, l.chunk_id, l.doc_id, l.idx, l.text, l.page, l.bboxes,
                   c.title, c.citation_label, c.url, c.found_on, c.site
            FROM lines l
            JOIN chunks c ON l.chunk_id = c.chunk_id
            WHERE l.text ILIKE %(pat)s ESCAPE '\\'
              AND (%(doc_id)s::text IS NULL OR l.doc_id = %(doc_id)s::text)
              AND (%(site)s::text IS NULL OR c.site = %(site)s::text)
            ORDER BY l.doc_id, c.ord, l.idx
            LIMIT %(limit)s
            """,
            {"pat": sql_pattern, "doc_id": doc_id, "site": site, "limit": limit},
        )
        rows = cur.fetchall()

    lines = []
    for r in rows:
        url = r.get("url") or ""
        text = r["text"]
        page = r.get("page")
        lines.append(
            {
                "line_id": r["line_id"],
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "idx": r["idx"],
                "text": text,
                "page": page,
                "bboxes": r.get("bboxes") or [],
                "title": r.get("title"),
                "citation_label": r.get("citation_label"),
                "url": url,
                "found_on": r.get("found_on"),
                "deep_link": make_deep_link(url, text, page),
            }
        )

    out: dict[str, Any] = {
        "pattern": pattern,
        "lines": lines,
        "total": len(lines),
    }
    return enforce_char_limit(out, MAX_TOOL_GREP_CHARS, "lines")


def toc_tool(
    pool: ConnectionPool | psycopg.Connection,
    doc_id: str,
) -> dict[str, Any]:
    """Tool: table of contents for a document in document order."""
    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.chunk_id, c.doc_id, c.ord, c.title, c.citation_label, c.section, c.legal_path,
                   COUNT(l.line_id) AS line_count
            FROM chunks c
            LEFT JOIN lines l ON c.chunk_id = l.chunk_id
            WHERE c.doc_id = %(doc_id)s
            GROUP BY c.chunk_id, c.doc_id, c.ord, c.title, c.citation_label, c.section, c.legal_path
            ORDER BY c.ord ASC, c.chunk_id ASC
            """,
            {"doc_id": doc_id},
        )
        rows = cur.fetchall()

    nodes = build_toc_nodes(rows)
    out: dict[str, Any] = {
        "doc_id": doc_id,
        "nodes": nodes,
        "total_nodes": len(nodes),
    }
    return enforce_char_limit(out, MAX_TOOL_TOC_CHARS, "nodes")


def open_tool(
    pool: ConnectionPool | psycopg.Connection,
    doc_id: str,
    *,
    node_id: str | None = None,
    chunk_id: str | None = None,
    max_lines: int = 60,
) -> dict[str, Any]:
    """Tool: open verbatim lines of a node or chunk in document order."""
    target_id = chunk_id or node_id

    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT l.line_id, l.chunk_id, l.doc_id, l.idx, l.text, l.page, l.bboxes,
                   c.title, c.citation_label, c.url, c.found_on, c.site, c.ord
            FROM lines l
            JOIN chunks c ON l.chunk_id = c.chunk_id
            WHERE l.doc_id = %(doc_id)s
              AND (%(target)s::text IS NULL OR l.chunk_id = %(target)s::text)
            ORDER BY c.ord ASC, l.idx ASC
            LIMIT %(limit)s
            """,
            {"doc_id": doc_id, "target": target_id, "limit": max_lines},
        )
        rows = cur.fetchall()

    lines = []
    for r in rows:
        url = r.get("url") or ""
        text = r["text"]
        page = r.get("page")
        lines.append(
            {
                "line_id": r["line_id"],
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "idx": r["idx"],
                "text": text,
                "page": page,
                "bboxes": r.get("bboxes") or [],
                "citation_label": r.get("citation_label"),
                "url": url,
                "found_on": r.get("found_on"),
                "deep_link": make_deep_link(url, text, page),
            }
        )

    out: dict[str, Any] = {
        "doc_id": doc_id,
        "node_id": target_id,
        "lines": lines,
        "total_lines": len(lines),
    }
    return enforce_char_limit(out, MAX_TOOL_OPEN_CHARS, "lines")
