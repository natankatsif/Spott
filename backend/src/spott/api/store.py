"""Postgres reads for answering: what retrieve() doesn't return (answering.Store)."""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from spott.core.embeddings import embed_texts

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
        rows = self._rows("SELECT doc_id, page_sizes, sha256 FROM documents WHERE doc_id = ANY(%s)", (doc_ids,))
        return {r["doc_id"]: r | {"has_file": raw_pdf(r.get("sha256")) is not None} for r in rows}

    def document(self, doc_id: str) -> dict | None:
        rows = self._rows("SELECT doc_id, kind, sha256, url FROM documents WHERE doc_id = %s", (doc_id,))
        return rows[0] if rows else None

    def preview_document(self, doc_id: str) -> dict | None:
        """What the source preview needs of a document (GET /api/preview/{doc_id})."""
        rows = self._rows(
            "SELECT doc_id, kind, url, title, site, found_on, page_sizes, sha256, "
            "COALESCE(updated_at, indexed_at)::text AS indexed_at FROM documents WHERE doc_id = %s", (doc_id,))
        if not rows:
            return None
        return rows[0] | {"has_file": raw_pdf(rows[0].get("sha256")) is not None}

    def doc_lines(self, doc_id: str) -> list[dict]:
        """Every line of a document in reading order, with its chunk's boxes (a line without its own boxes is
        shown with its chunk's on the same page)."""
        return self._rows(
            "SELECT l.line_id, l.text, l.page, l.bboxes, c.bboxes AS chunk_bboxes, c.pages FROM lines l "
            "JOIN chunks c USING (chunk_id) WHERE l.doc_id = %s "
            "ORDER BY (c.block_ids->>0)::int NULLS LAST, c.chunk_id, l.idx", (doc_id,))

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

    def dated_lines(self, doc_ids: list[str]) -> dict[str, list[str]]:
        """Lines with a year in them, per document: to tell how recent an undated document is."""
        by_doc: dict[str, list[str]] = {}
        for row in self._rows("SELECT doc_id, text FROM lines WHERE doc_id = ANY(%s) AND text ~ '\\d{4}'",
                              (doc_ids,)):
            by_doc.setdefault(row["doc_id"], []).append(row["text"])
        return by_doc

    def contacts_near(self, question: str, limit: int = 8) -> tuple[list[dict], dict | None]:
        """Contact cards nearest to the question (cosine similarity of bge-m3 embeddings), and the City Hall's
        general card; each card with the texts of its lines. ([], None) before `python -m spott.ingest.contacts` has run."""
        vec = embed_texts([question])[0]
        columns = "contact_id, name, area, phone, email, address, hours, url, site, line_ids, is_general"
        try:
            near = self._rows(f"SELECT {columns}, 1 - (embedding <=> %s) AS similarity FROM contacts "
                              "ORDER BY embedding <=> %s LIMIT %s", (vec, vec, limit))
            general = self._rows(f"SELECT {columns}, 0.0 AS similarity FROM contacts WHERE is_general "
                                 "ORDER BY jsonb_array_length(phone) DESC LIMIT 1", ())
        except psycopg.errors.UndefinedTable:
            return [], None
        cards = near + general
        texts = dict(self._rows_tuples("SELECT line_id, text FROM lines WHERE line_id = ANY(%s)",
                                       ([lid for c in cards for lid in c["line_ids"]],)))
        for c in cards:
            c["line_texts"] = [texts[lid] for lid in c["line_ids"] if lid in texts]
        return near, general[0] if general else None

    def _rows_tuples(self, sql: str, params: tuple) -> list[tuple]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
