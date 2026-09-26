"""Postgres reads for answering: what retrieve() doesn't return (answering.Store)."""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .files import raw_pdf

CHUNK_COLUMNS = (
    "chunk_id, doc_id, kind, lang, url, found_on, site, citation_label, text, "
    "title, doc_type, number, date, legal_path, has_contacts, pages, bboxes, block_ids, content_hash"
)
# Position of a chunk in its document: its first block.
POSITION = "(c.block_ids->>0)::int"


def like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PgStore:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def chunk_meta(self, chunk_ids: list[str]) -> dict[str, dict]:
        """All chunk fields. retrieve() leaves out citation fields (act number, date, point, boxes), and a chunk
        found only through its lines can come back without even doc_id."""
        rows = self._rows(f"SELECT {CHUNK_COLUMNS} FROM chunks c WHERE chunk_id = ANY(%s)", (chunk_ids,))
        return {r["chunk_id"]: r for r in rows}

    def next_chunks(self, anchors: list[tuple[str, int]]) -> list[dict]:
        """The chunk right after each (doc_id, position) in its document; anchor_pos tells which one."""
        return self._rows(
            f"""
            SELECT n.*, t.pos AS anchor_pos
            FROM unnest(%s::text[], %s::int[]) AS t(doc_id, pos)
            CROSS JOIN LATERAL (
                SELECT {CHUNK_COLUMNS} FROM chunks c
                WHERE c.doc_id = t.doc_id AND jsonb_array_length(c.block_ids) > 0 AND {POSITION} > t.pos
                ORDER BY {POSITION} LIMIT 1
            ) n
            """,
            ([d for d, _ in anchors], [p for _, p in anchors]),
        )

    def lines(self, chunk_ids: list[str]) -> dict[str, list[dict]]:
        by_chunk: dict[str, list[dict]] = {}
        for row in self._rows(
            "SELECT line_id, chunk_id, idx, text, page, bboxes FROM lines WHERE chunk_id = ANY(%s) "
            "ORDER BY chunk_id, idx",
            (chunk_ids,),
        ):
            by_chunk.setdefault(row["chunk_id"], []).append(row)
        return by_chunk

    def documents(self, doc_ids: list[str]) -> dict[str, dict]:
        # to_jsonb: indexes restored from older dumps have no sha256 column.
        rows = self._rows("SELECT doc_id, page_sizes, to_jsonb(d)->>'sha256' AS sha256 FROM documents d "
                          "WHERE doc_id = ANY(%s)", (doc_ids,))
        return {r["doc_id"]: r | {"has_file": raw_pdf(r["doc_id"], r.get("sha256")) is not None} for r in rows}

    def document(self, doc_id: str) -> dict | None:
        rows = self._rows("SELECT doc_id, kind, to_jsonb(d)->>'sha256' AS sha256, url FROM documents d "
                          "WHERE doc_id = %s", (doc_id,))
        return rows[0] if rows else None

    def later_acts(self, patterns: list[str], exclude_doc_ids: list[str], limit: int = 20) -> list[dict]:
        """Lines mentioning an act by number ("nr. 4/1", "4/1 din 05.03.2020"), outside given docs: later acts
        that amend, repeal or build on it. One line per chunk: {chunk_id, line_id}."""
        return self._rows(
            "SELECT DISTINCT ON (l.chunk_id) l.chunk_id, l.line_id FROM lines l "
            "WHERE l.text ILIKE ANY(%s) AND NOT (l.doc_id = ANY(%s)) ORDER BY l.chunk_id, l.idx LIMIT %s",
            ([f"%{like_escape(p)}%" for p in patterns], exclude_doc_ids, limit),
        )

    def grep_lines(self, keywords: list[str], limit: int = 200) -> list[dict]:
        """Lines containing any of the keywords verbatim (act numbers, names): {chunk_id, line_id, text}."""
        return self._rows(
            "SELECT chunk_id, line_id, text FROM lines WHERE text ILIKE ANY(%s) LIMIT %s",
            ([f"%{like_escape(k)}%" for k in keywords], limit),
        )

    def relation_lines(self, doc_ids: list[str]) -> list[dict]:
        """Amend/repeal lines touching these acts (act_relations, built by `python -m lineage`): lines of later
        acts that amend or repeal them, and their own lines amending or repealing other acts. Empty when
        lineage hasn't been run on this index."""
        try:
            return self._rows(
                "SELECT r.from_doc_id, r.to_doc_id, r.to_ref_text, r.relation, r.line_id, l.chunk_id "
                "FROM act_relations r JOIN lines l ON l.line_id = r.line_id "
                "WHERE (r.to_doc_id = ANY(%s) OR r.from_doc_id = ANY(%s)) AND r.relation IN ('amends', 'repeals') "
                "AND r.from_doc_id IS DISTINCT FROM r.to_doc_id",
                (doc_ids, doc_ids),
            )
        except psycopg.errors.UndefinedTable:
            return []

    def dated_lines(self, doc_ids: list[str]) -> dict[str, list[str]]:
        """Lines with a year in them, per document: to tell how recent an undated document is."""
        by_doc: dict[str, list[str]] = {}
        for row in self._rows("SELECT doc_id, text FROM lines WHERE doc_id = ANY(%s) AND text ~ '\\d{4}'",
                              (doc_ids,)):
            by_doc.setdefault(row["doc_id"], []).append(row["text"])
        return by_doc
