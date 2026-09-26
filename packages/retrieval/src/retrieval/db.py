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

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ro_unaccent') THEN
    CREATE TEXT SEARCH CONFIGURATION ro_unaccent (COPY = romanian);
    ALTER TEXT SEARCH CONFIGURATION ro_unaccent ALTER MAPPING FOR hword, hword_part, word WITH unaccent, romanian_stem;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_unaccent') THEN
    CREATE TEXT SEARCH CONFIGURATION ru_unaccent (COPY = russian);
    ALTER TEXT SEARCH CONFIGURATION ru_unaccent ALTER MAPPING FOR hword, hword_part, word WITH unaccent, russian_stem;
  END IF;
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
    embedding vector(1024),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector(
            CASE 
                WHEN lang = 'ru' THEN 'ru_unaccent'
                ELSE 'ro_unaccent'
            END::regconfig,
            immutable_unaccent(coalesce(title, '') || ' ' || coalesce(citation_label, '') || ' ' || text)
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_site ON chunks(site);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);
"""


def init_db(conn: psycopg.Connection | None = None) -> None:
    own_conn = conn is None
    c = conn or get_connection(autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(INIT_SQL)
    finally:
        if own_conn:
            c.close()
