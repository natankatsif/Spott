"""Database connection and schema initialization for pgvector."""

import os
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
import psycopg
from pgvector.psycopg import register_vector

load_dotenv(find_dotenv())

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "qwerty")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "qwerty_secret")
POSTGRES_DB = os.getenv("POSTGRES_DB", "qwerty")


def get_connection(autocommit: bool = True) -> psycopg.Connection:
    conn = psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
        autocommit=autocommit,
    )
    register_vector(conn)
    return conn


INIT_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION immutable_unaccent(text)
  RETURNS text AS $$
    SELECT public.unaccent($1);
$$ LANGUAGE sql IMMUTABLE STRICT;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT,
    doc_type TEXT,
    number TEXT,
    date TEXT,
    category TEXT,
    site TEXT,
    url TEXT,
    found_on TEXT,
    lang TEXT,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_site ON documents(site);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    citation_label TEXT NOT NULL,
    section JSONB,
    legal_path JSONB,
    block_ids JSONB,
    pages JSONB,
    bboxes JSONB,
    lang TEXT,
    char_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    has_contacts BOOLEAN NOT NULL DEFAULT FALSE,
    is_table BOOLEAN NOT NULL DEFAULT FALSE,
    title TEXT,
    doc_type TEXT,
    number TEXT,
    date TEXT,
    category TEXT,
    site TEXT,
    url TEXT,
    found_on TEXT,
    embedding vector(1024),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', immutable_unaccent(embed_text))) STORED
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_category ON chunks(category);
CREATE INDEX IF NOT EXISTS idx_chunks_lang ON chunks(lang);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
"""


def init_db(conn: psycopg.Connection | None = None) -> None:
    close_after = False
    if conn is None:
        conn = get_connection(autocommit=True)
        close_after = True
    try:
        with conn.cursor() as cur:
            cur.execute(INIT_SQL)
    finally:
        if close_after:
            conn.close()
