"""Database connection, pool management, and schema initialization for pgvector."""

import os
from collections.abc import Callable

import psycopg
from dotenv import find_dotenv, load_dotenv
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

load_dotenv(find_dotenv())

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "qwerty")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "qwerty_secret")
POSTGRES_DB = os.getenv("POSTGRES_DB", "qwerty")


def get_conninfo() -> str:
    return (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} user={POSTGRES_USER} "
        f"password={POSTGRES_PASSWORD} dbname={POSTGRES_DB}"
    )


def configure_connection(conn: psycopg.Connection) -> None:
    register_vector(conn)


def get_connection(autocommit: bool = True, register: bool = True) -> psycopg.Connection:
    conn = psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
        autocommit=autocommit,
    )
    if register:
        register_vector(conn)
    return conn


def get_pool(
    min_size: int = 2,
    max_size: int = 10,
    open: bool = True,
    configure: Callable[[psycopg.Connection], None] | None = None,
) -> ConnectionPool:
    """Returns a thread-safe psycopg ConnectionPool with pgvector registered."""
    return ConnectionPool(
        conninfo=get_conninfo(),
        min_size=min_size,
        max_size=max_size,
        configure=configure or configure_connection,
        open=open,
    )


INIT_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ro_unaccent') THEN
    CREATE TEXT SEARCH CONFIGURATION ro_unaccent (COPY = romanian);
  END IF;
  ALTER TEXT SEARCH CONFIGURATION ro_unaccent 
    ALTER MAPPING FOR asciiword, asciihword, hword_asciipart, word, hword, hword_part 
    WITH unaccent, romanian_stem;

  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_unaccent') THEN
    CREATE TEXT SEARCH CONFIGURATION ru_unaccent (COPY = russian);
  END IF;
  ALTER TEXT SEARCH CONFIGURATION ru_unaccent 
    ALTER MAPPING FOR asciiword, asciihword, hword_asciipart, word, hword, hword_part 
    WITH unaccent, russian_stem;
END
$$;

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
    page_sizes JSONB,
    sha256 TEXT,
    previous_sha256 TEXT,
    version INTEGER DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sha256 TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS previous_sha256 TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
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
    parent_legal_path JSONB,
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
    ord INTEGER DEFAULT 0,
    embedding vector(1024),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector(
            CASE
                WHEN lang = 'ru' THEN 'ru_unaccent'::regconfig
                ELSE 'ro_unaccent'::regconfig
            END,
            immutable_unaccent(coalesce(title, '') || ' ' || coalesce(citation_label, '') || ' ' || text)
        )
    ) STORED
);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS ord INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_site ON chunks(site);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_ord ON chunks(doc_id, ord);

CREATE TABLE IF NOT EXISTS lines (
    line_id TEXT PRIMARY KEY,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    lang TEXT,
    block_id TEXT,
    page INTEGER,
    bboxes JSONB,
    content_hash TEXT NOT NULL,
    embedding vector(1024),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector(
            CASE 
                WHEN lang = 'ru' THEN 'ru_unaccent'::regconfig
                ELSE 'ro_unaccent'::regconfig
            END,
            immutable_unaccent(embed_text)
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_lines_chunk_idx ON lines(chunk_id, idx);
CREATE INDEX IF NOT EXISTS idx_lines_doc_id ON lines(doc_id);
CREATE INDEX IF NOT EXISTS idx_lines_content_hash ON lines(content_hash);
CREATE INDEX IF NOT EXISTS idx_lines_tsv ON lines USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_lines_text_trgm ON lines USING GIN(text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lines_embedding ON lines USING HNSW(embedding vector_cosine_ops);

-- References between acts, built by `python -m lineage` from the lines above (every row cites its line).
-- No foreign keys: pg_restore --clean of older dumps must be able to drop and recreate documents/lines.
CREATE TABLE IF NOT EXISTS act_relations (
    from_doc_id TEXT NOT NULL,
    to_doc_id TEXT,              -- NULL when the referenced act isn't in the corpus
    to_ref_text TEXT NOT NULL,   -- "Dispoziția 185-d din 23.04.2020"
    to_doc_type TEXT,
    to_number TEXT,
    to_date TEXT,
    relation TEXT NOT NULL,      -- amends | repeals | refers
    line_id TEXT NOT NULL,
    PRIMARY KEY (from_doc_id, line_id, to_ref_text)
);
CREATE INDEX IF NOT EXISTS idx_act_relations_to ON act_relations(to_doc_id);
"""


def init_db(conn: psycopg.Connection | None = None) -> None:
    own_conn = conn is None
    # No vector registration: on a fresh database the extension doesn't exist until INIT_SQL creates it.
    c = conn or get_connection(autocommit=True, register=False)
    try:
        with c.cursor() as cur:
            cur.execute(INIT_SQL)
    finally:
        if own_conn:
            c.close()
