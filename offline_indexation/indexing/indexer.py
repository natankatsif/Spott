"""Incremental indexing of chunks and documents into PostgreSQL with pgvector."""

import json
import logging
import time
from typing import Any

import numpy as np
import psycopg
from .db import get_connection, init_db
from .embeddings import get_device, get_embedding_model

log = logging.getLogger("indexing.indexer")

MODEL_NAME = "BAAI/bge-m3"


class Indexer:
    def __init__(self, conn: psycopg.Connection | None = None, batch_size: int = 32):
        self.conn = conn or get_connection(autocommit=True)
        self.batch_size = batch_size
        self._model = None
        self.device = get_device()

    @property
    def model(self):
        if self._model is None:
            self._model = get_embedding_model(self.device)
        return self._model

    def find_cached_embeddings(self, content_hashes: list[str]) -> dict[str, list[float]]:
        """Returns existing embeddings for matching content hashes."""
        if not content_hashes:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash, embedding FROM chunks "
                "WHERE content_hash = ANY(%s) AND embedding IS NOT NULL",
                (content_hashes,),
            )
            rows = cur.fetchall()
            cached = {}
            for h, emb in rows:
                if h not in cached and emb is not None:
                    # emb from pgvector is numpy array or list
                    cached[h] = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            return cached

    def upsert_document_meta(self, doc_id: str, first: dict) -> None:
        """Upserts document row into documents table."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (doc_id, kind, title, doc_type, number, date, category, site, url, found_on, lang, page_sizes, indexed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (doc_id) DO UPDATE SET
                  kind = EXCLUDED.kind,
                  title = EXCLUDED.title,
                  doc_type = EXCLUDED.doc_type,
                  number = EXCLUDED.number,
                  date = EXCLUDED.date,
                  category = EXCLUDED.category,
                  site = EXCLUDED.site,
                  url = EXCLUDED.url,
                  found_on = EXCLUDED.found_on,
                  lang = EXCLUDED.lang,
                  page_sizes = EXCLUDED.page_sizes,
                  indexed_at = NOW();
                """,
                (
                    doc_id,
                    first.get("kind", "file"),
                    first.get("title"),
                    first.get("doc_type"),
                    first.get("number"),
                    first.get("date"),
                    first.get("category"),
                    first.get("site"),
                    first.get("url"),
                    first.get("found_on"),
                    first.get("lang"),
                    json.dumps(first.get("page_sizes", []), ensure_ascii=False),
                ),
            )
        self.conn.commit()

    def upsert_chunks_batch(self, chunks: list[dict]) -> None:
        """Upserts a batch of chunks into the chunks table immediately."""
        if not chunks:
            return
        with self.conn.cursor() as cur:
            for c in chunks:
                parent_lp = c.get("parent_legal_path")
                if parent_lp is None:
                    parent_lp = c.get("legal_path", [])[:-1] if c.get("legal_path") else []

                emb = c.get("embedding")
                emb_arr = np.array(emb, dtype=np.float32) if emb is not None else None

                cur.execute(
                    """
                    INSERT INTO chunks (
                      chunk_id, doc_id, kind, text, embed_text, citation_label,
                      section, legal_path, parent_legal_path, block_ids, pages, bboxes, lang,
                      char_count, content_hash, has_contacts, is_table,
                      title, doc_type, number, date, category, site, url, found_on, embedding
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (chunk_id) DO UPDATE SET
                      text = EXCLUDED.text,
                      embed_text = EXCLUDED.embed_text,
                      citation_label = EXCLUDED.citation_label,
                      section = EXCLUDED.section,
                      legal_path = EXCLUDED.legal_path,
                      parent_legal_path = EXCLUDED.parent_legal_path,
                      block_ids = EXCLUDED.block_ids,
                      pages = EXCLUDED.pages,
                      bboxes = EXCLUDED.bboxes,
                      lang = EXCLUDED.lang,
                      char_count = EXCLUDED.char_count,
                      content_hash = EXCLUDED.content_hash,
                      has_contacts = EXCLUDED.has_contacts,
                      is_table = EXCLUDED.is_table,
                      title = EXCLUDED.title,
                      doc_type = EXCLUDED.doc_type,
                      number = EXCLUDED.number,
                      date = EXCLUDED.date,
                      category = EXCLUDED.category,
                      site = EXCLUDED.site,
                      url = EXCLUDED.url,
                      found_on = EXCLUDED.found_on,
                      embedding = EXCLUDED.embedding;
                    """,
                    (
                        c["chunk_id"],
                        c["doc_id"],
                        c.get("kind", "file"),
                        c["text"],
                        c["embed_text"],
                        c["citation_label"],
                        json.dumps(c.get("section", []), ensure_ascii=False) if isinstance(c.get("section"), list) else c.get("section", ""),
                        json.dumps(c.get("legal_path", []), ensure_ascii=False),
                        json.dumps(parent_lp, ensure_ascii=False),
                        json.dumps(c.get("block_ids", []), ensure_ascii=False),
                        json.dumps(c.get("pages", []), ensure_ascii=False),
                        json.dumps(c.get("bboxes", []), ensure_ascii=False),
                        c.get("lang"),
                        c.get("char_count", len(c["text"])),
                        c["content_hash"],
                        c.get("has_contacts", False),
                        c.get("is_table", False),
                        c.get("title"),
                        c.get("doc_type"),
                        c.get("number"),
                        c.get("date"),
                        c.get("category"),
                        c.get("site"),
                        c.get("url"),
                        c.get("found_on"),
                        emb_arr,
                    ),
                )
        self.conn.commit()

    def compute_embeddings(self, chunks: list[dict]) -> tuple[int, int]:
        """Assigns embeddings to chunks, reusing cached ones whenever possible.
        Returns (reused_count, computed_count).
        """
        if not chunks:
            return 0, 0

        hashes = list({c["content_hash"] for c in chunks})
        cached = self.find_cached_embeddings(hashes)

        to_embed = []
        to_embed_indices = []
        for i, c in enumerate(chunks):
            chash = c["content_hash"]
            if chash in cached:
                c["embedding"] = cached[chash]
            else:
                to_embed.append(c["embed_text"])
                to_embed_indices.append(i)

        reused_count = len(chunks) - len(to_embed)
        computed_count = len(to_embed)

        if to_embed:
            import torch

            log.info("Computing embeddings for %d chunks (reused %d)...", computed_count, reused_count)
            # Sort by text length to minimize padding overhead in batches
            sorted_pairs = sorted(
                zip(to_embed_indices, to_embed),
                key=lambda p: len(p[1]),
            )
            sorted_indices = [p[0] for p in sorted_pairs]
            sorted_texts = [p[1] for p in sorted_pairs]

            slice_size = 128
            t0 = time.time()
            for start_idx in range(0, len(sorted_texts), slice_size):
                sub_texts = sorted_texts[start_idx : start_idx + slice_size]
                sub_indices = sorted_indices[start_idx : start_idx + slice_size]
                sub_vectors = self.model.encode(
                    sub_texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                for idx, vec in zip(sub_indices, sub_vectors):
                    emb_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
                    chunks[idx]["embedding"] = emb_list
                    cached[chunks[idx]["content_hash"]] = emb_list

                # Save batch to DB immediately so work is never lost
                self.upsert_chunks_batch([chunks[idx] for idx in sub_indices])

                if hasattr(torch, "mps") and torch.backends.mps.is_available():
                    torch.mps.empty_cache()

                done = min(start_idx + slice_size, len(sorted_texts))
                rate = done / max(time.time() - t0, 0.001)
                log.info(
                    "Embedded & saved %d / %d chunks (%.1f%%) at %.1f chunks/s...",
                    done,
                    len(sorted_texts),
                    done * 100.0 / len(sorted_texts),
                    rate,
                )

        return reused_count, computed_count

    def index_document(self, doc_id: str, chunks: list[dict]) -> None:
        """Indexes all chunks for a single document, removing stale chunks."""
        if not chunks:
            # Delete document if it has no chunks
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
            return

        first = chunks[0]
        with self.conn.cursor() as cur:
            # 1. Upsert document
            cur.execute(
                """
                INSERT INTO documents (doc_id, kind, title, doc_type, number, date, category, site, url, found_on, lang, page_sizes, indexed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (doc_id) DO UPDATE SET
                  kind = EXCLUDED.kind,
                  title = EXCLUDED.title,
                  doc_type = EXCLUDED.doc_type,
                  number = EXCLUDED.number,
                  date = EXCLUDED.date,
                  category = EXCLUDED.category,
                  site = EXCLUDED.site,
                  url = EXCLUDED.url,
                  found_on = EXCLUDED.found_on,
                  lang = EXCLUDED.lang,
                  page_sizes = EXCLUDED.page_sizes,
                  indexed_at = NOW();
                """,
                (
                    doc_id,
                    first.get("kind", "file"),
                    first.get("title"),
                    first.get("doc_type"),
                    first.get("number"),
                    first.get("date"),
                    first.get("category"),
                    first.get("site"),
                    first.get("url"),
                    first.get("found_on"),
                    first.get("lang"),
                    json.dumps(first.get("page_sizes", []), ensure_ascii=False),
                ),
            )

            # 2. Upsert chunks
            chunk_ids = []
            for c in chunks:
                chunk_ids.append(c["chunk_id"])
                parent_lp = c.get("parent_legal_path")
                if parent_lp is None:
                    parent_lp = c.get("legal_path", [])[:-1] if c.get("legal_path") else []

                cur.execute(
                    """
                    INSERT INTO chunks (
                      chunk_id, doc_id, kind, text, embed_text, citation_label,
                      section, legal_path, parent_legal_path, block_ids, pages, bboxes, lang,
                      char_count, content_hash, has_contacts, is_table,
                      title, doc_type, number, date, category, site, url, found_on, embedding
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (chunk_id) DO UPDATE SET
                      text = EXCLUDED.text,
                      embed_text = EXCLUDED.embed_text,
                      citation_label = EXCLUDED.citation_label,
                      section = EXCLUDED.section,
                      legal_path = EXCLUDED.legal_path,
                      parent_legal_path = EXCLUDED.parent_legal_path,
                      block_ids = EXCLUDED.block_ids,
                      pages = EXCLUDED.pages,
                      bboxes = EXCLUDED.bboxes,
                      lang = EXCLUDED.lang,
                      char_count = EXCLUDED.char_count,
                      content_hash = EXCLUDED.content_hash,
                      has_contacts = EXCLUDED.has_contacts,
                      is_table = EXCLUDED.is_table,
                      title = EXCLUDED.title,
                      doc_type = EXCLUDED.doc_type,
                      number = EXCLUDED.number,
                      date = EXCLUDED.date,
                      category = EXCLUDED.category,
                      site = EXCLUDED.site,
                      url = EXCLUDED.url,
                      found_on = EXCLUDED.found_on,
                      embedding = EXCLUDED.embedding;
                    """,
                    (
                        c["chunk_id"],
                        c["doc_id"],
                        c["kind"],
                        c["text"],
                        c["embed_text"],
                        c["citation_label"],
                        json.dumps(c.get("section", []), ensure_ascii=False),
                        json.dumps(c.get("legal_path", []), ensure_ascii=False),
                        json.dumps(parent_lp, ensure_ascii=False),
                        json.dumps(c.get("block_ids", []), ensure_ascii=False),
                        json.dumps(c.get("pages", []), ensure_ascii=False),
                        json.dumps(c.get("bboxes", []), ensure_ascii=False),
                        c.get("lang"),
                        c["char_count"],
                        c["content_hash"],
                        c.get("has_contacts", False),
                        c.get("is_table", False),
                        c.get("title"),
                        c.get("doc_type"),
                        c.get("number"),
                        c.get("date"),
                        c.get("category"),
                        c.get("site"),
                        c.get("url"),
                        c.get("found_on"),
                        np.array(c["embedding"], dtype=np.float32) if c.get("embedding") else None,
                    ),
                )

            # 3. Delete stale chunks for this doc_id
            cur.execute(
                "DELETE FROM chunks WHERE doc_id = %s AND chunk_id != ALL(%s)",
                (doc_id, chunk_ids),
            )

    def clean_orphaned_documents(self, active_doc_ids: set[str]) -> int:
        """Removes documents from Postgres that no longer exist in the chunker output."""
        if not active_doc_ids:
            return 0
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM documents WHERE doc_id != ALL(%s) RETURNING doc_id",
                (list(active_doc_ids),),
            )
            deleted = cur.fetchall()
            return len(deleted)
