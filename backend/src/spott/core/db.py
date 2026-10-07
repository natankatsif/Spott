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
"""


# Contacts extracted from the index by `python -m spott.ingest.contacts`: part of the index (goes into the dump).
CONTACTS_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,              -- institution / department
    area TEXT,                       -- what it handles, one sentence from the page
    phone JSONB NOT NULL DEFAULT '[]',
    email JSONB NOT NULL DEFAULT '[]',
    address TEXT,
    hours TEXT,
    url TEXT NOT NULL,
    site TEXT NOT NULL,
    category TEXT,
    doc_id TEXT NOT NULL,
    line_ids JSONB NOT NULL,         -- every phone, e-mail and address is in one of these lines
    is_general BOOLEAN NOT NULL DEFAULT FALSE,  -- the City Hall's general contact
    embedding vector(1024)
);
CREATE INDEX IF NOT EXISTS idx_contacts_site ON contacts(site);
"""

# App state, not part of the index dump: sources the admin manages and their jobs, answers given, ratings,
# quick questions. Created by the backend at startup and by the offline tools that use them.
APP_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('site', 'document')),
    url TEXT NOT NULL,
    site_id TEXT NOT NULL,           -- domain
    category TEXT,
    start_urls JSONB NOT NULL DEFAULT '[]',
    max_depth INTEGER,
    max_pages INTEGER,
    delay REAL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    robots TEXT NOT NULL DEFAULT 'allowed' CHECK (robots IN ('allowed', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_job_id BIGINT,
    title TEXT,                      -- sources added by URL: the page's title
    category_source TEXT,            -- how the category was decided
    -- Automatic updates (docs/history/audit/06-freshness-plan.md): a nightly `check` of what changed, a weekly full
    -- `refresh`, earlier when people signal outdated content.
    auto_update BOOLEAN NOT NULL DEFAULT TRUE,
    check_method TEXT,               -- wordpress | sitemap | fingerprint (+…)
    last_checked_at TIMESTAMPTZ,     -- the last check or full refresh
    last_full_at TIMESTAMPTZ,        -- the last complete refresh
    next_check_at TIMESTAMPTZ,       -- when the scheduler queues the next one
    fingerprints JSONB NOT NULL DEFAULT '{}',  -- key page → hash of its text
    stale_signals INTEGER NOT NULL DEFAULT 0,  -- since the last check
    last_signal_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_site ON sources(site_id) WHERE kind = 'site';
CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_document ON sources(url) WHERE kind = 'document';

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT,                -- NULL = all sources
    -- `check` and `refresh`: automatic updates; `backlog`: the autopilot's own job, queued by the worker when it has
    -- nothing else to do, so a source is finished methodically across as many nights as it takes.
    kind TEXT NOT NULL CHECK (kind IN ('crawl', 'refresh', 'check', 'backlog')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    stage TEXT,
    stage_done INTEGER NOT NULL DEFAULT 0,
    stage_total INTEGER NOT NULL DEFAULT 0,
    percent REAL NOT NULL DEFAULT 0,
    eta_s REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    stats JSONB NOT NULL DEFAULT '{}',
    log_tail JSONB NOT NULL DEFAULT '[]',
    error TEXT,
    url TEXT                         -- a job for one URL of a source (a deeper path, a document of a known domain)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS answers (
    answer_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id TEXT,
    question TEXT NOT NULL,
    lang TEXT NOT NULL,
    status TEXT NOT NULL,
    verified BOOLEAN NOT NULL,
    path TEXT NOT NULL,
    citations INTEGER NOT NULL,
    doc_ids JSONB NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL,
    -- The admin's gaps: what a partial answer lacked, sites found but not used, hidden by the admin, the last re-check.
    missing JSONB,
    retrieved_sites JSONB,
    gap_hidden BOOLEAN NOT NULL DEFAULT FALSE,
    topic TEXT,                      -- of an unanswered question, set once by a small model (spott/api/gaps.py)
    gap_group TEXT,                  -- the group a wording group was sorted into
    gap_title JSONB,                 -- {"ro", "ru"} on a group's first answer
    recheck JSONB
);
CREATE INDEX IF NOT EXISTS idx_answers_created ON answers(created_at);

CREATE TABLE IF NOT EXISTS feedback (
    answer_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    tags JSONB NOT NULL DEFAULT '[]',
    comment TEXT,
    citation_id TEXT,
    question TEXT,
    lang TEXT,
    status TEXT,
    doc_ids JSONB NOT NULL DEFAULT '[]',
    path TEXT,
    answer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (answer_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);

CREATE TABLE IF NOT EXISTS suggestions (
    id BIGSERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    lang TEXT NOT NULL,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    hidden BOOLEAN NOT NULL DEFAULT FALSE,
    asked_count INTEGER NOT NULL DEFAULT 0,
    rating_avg REAL,
    answer_id TEXT,
    ok BOOLEAN NOT NULL DEFAULT FALSE,   -- the last re-check answered it, verified
    checked_at TIMESTAMPTZ,
    index_version TEXT,
    cached JSONB,                        -- the last verified AskResponse, replayed on click
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pin_group TEXT,                      -- the same pinned question in each language (pinned in RO, translated to RU)
    texts JSONB,                         -- a pinned group's text in RO, RU and EN
    UNIQUE (lang, question)
);

-- Admin settings (backend/src/spott/api/llm_settings.py: API keys and the model of each role), one JSON value per key.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Admin → Spending (backend/src/spott/api/usage.py): one row per model call, its tokens; money comes from the admin's prices.
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,          -- answer | fast | deep
    kind TEXT NOT NULL,          -- answer, route, rewrite, translate_quotes, translate, gap_groups...
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);

-- Unique visitors: one row per browser (its anonymous localStorage id), for the counter in the header.
CREATE TABLE IF NOT EXISTS visitors (
    visitor_id TEXT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


# The crawl registry (spott.ingest.common.registry): what the ingest stages found, downloaded and parsed, so every
# stage is incremental. Not part of the index dump: it stays with the machine that crawls.
REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS registry_pages (
    url           TEXT PRIMARY KEY,          -- final URL after redirects
    site          TEXT NOT NULL,
    status        INTEGER,                   -- HTTP status; 410 = not reached by the last complete crawl
    depth         INTEGER,
    parent        TEXT,
    anchor_text   TEXT,
    title         TEXT,
    lang          TEXT,
    alternates    JSONB NOT NULL DEFAULT '{}',  -- {hreflang: url}
    html_file     TEXT,                      -- relative to data/crawl/<site>/
    content_type  TEXT,
    error         TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL,
    parse_status  TEXT NOT NULL DEFAULT 'pending',  -- pending | parsed | empty | failed
    html_hash     TEXT,
    parsed_at     TIMESTAMPTZ,
    parse_error   TEXT
);

-- Unique downloaded contents, keyed by SHA-256.
CREATE TABLE IF NOT EXISTS registry_files (
    sha256          TEXT PRIMARY KEY,
    path            TEXT NOT NULL,           -- relative to data/
    size            BIGINT NOT NULL,
    content_type    TEXT,
    extension       TEXT,
    downloaded_at   TIMESTAMPTZ NOT NULL,
    parse_status    TEXT NOT NULL DEFAULT 'pending',  -- pending | parsing | parsed | failed | unsupported
    parser_version  TEXT,
    parsed_at       TIMESTAMPTZ,
    parse_error     TEXT
);

-- One row per document URL and the state of its download.
CREATE TABLE IF NOT EXISTS registry_documents (
    key             TEXT PRIMARY KEY,        -- url_key(): ignores scheme, www., trailing slash
    url             TEXT NOT NULL,
    site            TEXT NOT NULL,           -- site where first discovered
    category        TEXT,
    extension       TEXT,
    external        BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'discovered',  -- discovered, downloaded, not_a_file, failed, removed, missing
    sha256          TEXT REFERENCES registry_files(sha256),  -- current content
    etag            TEXT,
    last_modified   TEXT,                    -- the Last-Modified header as the site sent it
    http_status     INTEGER,
    error           TEXT,
    discovered_at   TIMESTAMPTZ NOT NULL,
    checked_at      TIMESTAMPTZ,             -- last download attempt
    version         INTEGER NOT NULL DEFAULT 1,
    previous_sha256 TEXT,
    updated_at      TIMESTAMPTZ,
    consecutive_missing INTEGER NOT NULL DEFAULT 0,
    removed_at      TIMESTAMPTZ
);

-- Every page where a document link was found (provenance for citations).
CREATE TABLE IF NOT EXISTS registry_document_sources (
    document_key   TEXT NOT NULL REFERENCES registry_documents(key),
    found_on       TEXT NOT NULL DEFAULT '', -- page URL; '' when unknown
    found_on_title TEXT,
    anchor_text    TEXT,
    site           TEXT NOT NULL,
    depth          INTEGER,
    via            TEXT,                     -- a | iframe | embed | object | content-type | wp-media | freshness
    published      TEXT,                     -- upload date from WordPress media, as the site gives it
    discovered_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (document_key, found_on)
);

-- History of the contents seen at each document URL.
CREATE TABLE IF NOT EXISTS registry_document_versions (
    document_key  TEXT NOT NULL REFERENCES registry_documents(key),
    sha256        TEXT NOT NULL REFERENCES registry_files(sha256),
    fetched_at    TIMESTAMPTZ NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (document_key, sha256)
);

-- Indexes only when missing: CREATE INDEX IF NOT EXISTS waits for a stage that is writing, and every stage runs this.
DO $$
BEGIN
  IF to_regclass('idx_registry_pages_site') IS NULL THEN
    CREATE INDEX idx_registry_pages_site ON registry_pages(site);
  END IF;
  IF to_regclass('idx_registry_files_parse_status') IS NULL THEN
    CREATE INDEX idx_registry_files_parse_status ON registry_files(parse_status);
  END IF;
  IF to_regclass('idx_registry_documents_site') IS NULL THEN
    CREATE INDEX idx_registry_documents_site ON registry_documents(site);
  END IF;
  IF to_regclass('idx_registry_documents_status') IS NULL THEN
    CREATE INDEX idx_registry_documents_status ON registry_documents(status);
  END IF;
  IF to_regclass('idx_registry_documents_sha256') IS NULL THEN
    CREATE INDEX idx_registry_documents_sha256 ON registry_documents(sha256);
  END IF;
END
$$;
"""


def init_db(conn: psycopg.Connection | None = None) -> None:
    own_conn = conn is None
    # No vector registration: on a fresh database the extension doesn't exist until INIT_SQL creates it.
    c = conn or get_connection(autocommit=True, register=False)
    try:
        with c.cursor() as cur:
            cur.execute(INIT_SQL)
            cur.execute(CONTACTS_SQL)
            cur.execute(APP_SQL)
            cur.execute(REGISTRY_SQL)
    finally:
        if own_conn:
            c.close()


def init_app_db(conn: psycopg.Connection | None = None) -> None:
    """The app-state tables, contacts and the registry, on an existing index: what the backend needs at startup."""
    own_conn = conn is None
    c = conn or get_connection(autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(CONTACTS_SQL)
            cur.execute(APP_SQL)
            cur.execute(REGISTRY_SQL)
    finally:
        if own_conn:
            c.close()


def init_registry_db(conn: psycopg.Connection) -> None:
    """The registry tables only: what the ingest stages need (spott.ingest.common.registry)."""
    conn.execute(REGISTRY_SQL)
