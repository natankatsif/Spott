"""Incremental indexing of chunks into PostgreSQL + pgvector.

Flow (see __main__.py):
    upsert_documents  -> documents rows exist before their chunks (FK)
    embed_and_store   -> reuse embeddings by content_hash, embed the rest in
                         length-sorted batches, write each batch immediately
                         (an interrupted run resumes where it stopped)
    delete_stale_chunks / clean_orphaned_documents
"""

import json
import logging
import time

import numpy as np
import psycopg
from retrieval.db import get_connection
from retrieval.embeddings import free_device_cache, get_device, get_embedding_model

log = logging.getLogger("indexing.indexer")

WRITE_BATCH = 128  # chunks embedded and written per step

DOCUMENT_COLUMNS = (
    "doc_id", "kind", "title", "doc_type", "number", "date", "category", "site",
    "url", "found_on", "lang", "page_sizes",
    "sha256", "previous_sha256", "version", "updated_at",
)
CHUNK_COLUMNS = ("chunk_id", "doc_id", "kind", "text", "embed_text", "citation_label",
                 "section", "legal_path", "parent_legal_path", "block_ids", "pages", "bboxes", "lang",
                 "char_count", "content_hash", "has_contacts", "is_table",
                 "title", "doc_type", "number", "date", "category", "site", "url", "found_on", "ord", "embedding")
LINE_COLUMNS = ("line_id", "chunk_id", "doc_id", "idx", "text", "embed_text",
                "lang", "block_id", "page", "bboxes", "content_hash", "embedding")
JSON_COLUMNS = {"page_sizes", "section", "legal_path", "parent_legal_path", "block_ids", "pages", "bboxes"}


def upsert_sql(table: str, columns: tuple[str, ...], key: str, touch: str | None = None) -> str:
    """INSERT … ON CONFLICT (key) DO UPDATE SET every other column (+ touch = NOW())."""
    names = list(columns) + ([touch] if touch else [])
    values = ["%s"] * len(columns) + (["NOW()"] if touch else [])
    updates = [f"{c} = EXCLUDED.{c}" for c in names if c != key]
    return (f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(values)}) "
            f"ON CONFLICT ({key}) DO UPDATE SET {', '.join(updates)}")


UPSERT_DOCUMENT = upsert_sql("documents", DOCUMENT_COLUMNS, "doc_id", touch="indexed_at")
UPSERT_CHUNK = upsert_sql("chunks", CHUNK_COLUMNS, "chunk_id")
UPSERT_LINE = upsert_sql("lines", LINE_COLUMNS, "line_id")


def row_values(record: dict, columns: tuple[str, ...]) -> list:
    """Chunk/document/line dict -> SQL parameters in column order (JSON-encodes list fields)."""
    values = []
    for col in columns:
        value = record.get(col)
        if col in JSON_COLUMNS:
            value = json.dumps(value or [], ensure_ascii=False)
        elif col == "embedding" and value is not None:
            value = value.to_numpy() if hasattr(value, "to_numpy") else np.asarray(value, dtype=np.float32)
        elif col in ("has_contacts", "is_table"):
            value = bool(value)
        elif col in ("ord", "idx") and value is None:
            value = 0
        elif col == "version" and value is None:
            value = 1
        values.append(value)
    return values


def document_record(doc_id: str, first_chunk: dict) -> dict:
    """Document metadata is copied onto every chunk by the chunker; take it from the first one."""
    default_kind = "page" if doc_id.startswith("page:") else "file"
    return (
        {"kind": first_chunk.get("kind", default_kind)}
        | {c: first_chunk.get(c) for c in DOCUMENT_COLUMNS if c in first_chunk}
        | {"doc_id": doc_id}
    )



class Indexer:
    def __init__(self, conn: psycopg.Connection | None = None, batch_size: int = 32):
        self.conn = conn or get_connection(autocommit=True)
        self.batch_size = batch_size
        self.device = get_device()

    @property
    def model(self):
        return get_embedding_model(self.device)

    # --- documents -----------------------------------------------------------

    def upsert_documents(self, by_doc: dict[str, list[dict]]) -> None:
        rows = [row_values(document_record(doc_id, chunks[0]), DOCUMENT_COLUMNS)
                for doc_id, chunks in by_doc.items() if chunks]
        with self.conn.cursor() as cur:
            cur.executemany(UPSERT_DOCUMENT, rows)

    # --- chunks --------------------------------------------------------------

    def find_cached_embeddings(self, content_hashes: list[str]) -> dict[str, list[float]]:
        """Embeddings already in the index for these texts (keyed by content_hash)."""
        if not content_hashes:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (content_hash) content_hash, embedding FROM chunks "
                "WHERE content_hash = ANY(%s) AND embedding IS NOT NULL",
                (content_hashes,),
            )
            return {h: emb for h, emb in cur.fetchall()}

    def write_chunks(self, chunks: list[dict]) -> None:
        if not chunks:
            return
        with self.conn.cursor() as cur:
            cur.executemany(UPSERT_CHUNK, [row_values(c, CHUNK_COLUMNS) for c in chunks])

    def embed_and_store(self, chunks: list[dict]) -> tuple[int, int]:
        """Writes all chunks with embeddings. Returns (reused, computed)."""
        if not chunks:
            return 0, 0
        cached = self.find_cached_embeddings(list({c["content_hash"] for c in chunks}))

        reused = [c for c in chunks if c["content_hash"] in cached]
        for c in reused:
            c["embedding"] = cached[c["content_hash"]]
        for start in range(0, len(reused), WRITE_BATCH):
            self.write_chunks(reused[start:start + WRITE_BATCH])

        # Length-sorted batches waste less compute on padding.
        todo = sorted((c for c in chunks if c["content_hash"] not in cached), key=lambda c: len(c["embed_text"]))
        if todo:
            log.info("Embedding %d chunks (reused %d) on %s", len(todo), len(reused), self.device)
        started = time.monotonic()
        for start in range(0, len(todo), WRITE_BATCH):
            batch = todo[start:start + WRITE_BATCH]
            vectors = self.model.encode([c["embed_text"] for c in batch], batch_size=self.batch_size,
                                        normalize_embeddings=True, show_progress_bar=False)
            for c, vec in zip(batch, vectors, strict=True):
                c["embedding"] = vec
            self.write_chunks(batch)
            free_device_cache(self.device)
            done = start + len(batch)
            log.info("Embedded %d/%d (%.1f chunks/s)", done, len(todo), done / max(time.monotonic() - started, 1e-3))
        return len(reused), len(todo)

    # --- lines ---------------------------------------------------------------

    def find_cached_line_embeddings(self, content_hashes: list[str]) -> dict[str, list[float]]:
        """Embeddings already in lines table for these texts (keyed by content_hash)."""
        if not content_hashes:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (content_hash) content_hash, embedding FROM lines "
                "WHERE content_hash = ANY(%s) AND embedding IS NOT NULL",
                (content_hashes,),
            )
            return {h: emb for h, emb in cur.fetchall()}

    def write_lines(self, lines: list[dict]) -> None:
        if not lines:
            return
        with self.conn.cursor() as cur:
            cur.executemany(UPSERT_LINE, [row_values(l, LINE_COLUMNS) for l in lines])

    def embed_and_store_lines(self, lines: list[dict]) -> tuple[int, int]:
        """Writes all lines with embeddings. Returns (reused, computed)."""
        if not lines:
            return 0, 0
        cached = self.find_cached_line_embeddings(list({l["content_hash"] for l in lines}))

        # Deduplicate texts to encode by content_hash
        to_encode_hashes: dict[str, str] = {}
        for l in lines:
            h = l["content_hash"]
            if h not in cached and h not in to_encode_hashes:
                to_encode_hashes[h] = l["embed_text"]

        todo_items = sorted(to_encode_hashes.items(), key=lambda item: len(item[1]))
        if todo_items:
            log.info("Embedding %d unique lines (reused %d) on %s", len(todo_items), len(cached), self.device)
        started = time.monotonic()
        for start in range(0, len(todo_items), WRITE_BATCH):
            batch = todo_items[start : start + WRITE_BATCH]
            vectors = self.model.encode(
                [text for _, text in batch],
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            for (h, _), vec in zip(batch, vectors, strict=True):
                cached[h] = vec
            free_device_cache(self.device)
            done = start + len(batch)
            elapsed = max(time.monotonic() - started, 1e-3)
            log.info("Embedded %d/%d unique lines (%.1f lines/s)", done, len(todo_items), done / elapsed)

        # Assign cached embeddings to all lines and write in batches
        for l in lines:
            l["embedding"] = cached[l["content_hash"]]

        for start in range(0, len(lines), WRITE_BATCH):
            self.write_lines(lines[start : start + WRITE_BATCH])

        reused_count = len(lines) - len(todo_items)
        return reused_count, len(todo_items)

    # --- cleanup -------------------------------------------------------------

    def delete_stale_chunks(self, by_doc: dict[str, list[dict]]) -> int:
        """Removes chunks of indexed documents that the chunker no longer produces."""
        deleted = 0
        with self.conn.cursor() as cur:
            for doc_id, chunks in by_doc.items():
                cur.execute("DELETE FROM chunks WHERE doc_id = %s AND chunk_id <> ALL(%s)",
                            (doc_id, [c["chunk_id"] for c in chunks]))
                deleted += cur.rowcount
        return deleted

    def delete_stale_lines(self, by_chunk: dict[str, list[dict]]) -> int:
        """Removes lines of indexed chunks that are no longer produced."""
        deleted = 0
        with self.conn.cursor() as cur:
            for chunk_id, lines in by_chunk.items():
                if lines:
                    cur.execute(
                        "DELETE FROM lines WHERE chunk_id = %s AND line_id <> ALL(%s)",
                        (chunk_id, [l["line_id"] for l in lines]),
                    )
                    deleted += cur.rowcount
        return deleted

    def clean_orphaned_documents(self, active_doc_ids: set[str]) -> int:
        """Removes documents (and their chunks, via CASCADE) no longer in the chunker output."""
        if not active_doc_ids:

            return 0
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE doc_id <> ALL(%s)", (list(active_doc_ids),))
            return cur.rowcount
