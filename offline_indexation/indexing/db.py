"""Database connection and schema initialization for pgvector (re-exported from retrieval)."""

from retrieval.db import (
    INIT_SQL,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    configure_connection,
    get_connection,
    get_conninfo,
    get_pool,
    init_db,
)

__all__ = [
    "INIT_SQL",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "configure_connection",
    "get_connection",
    "get_conninfo",
    "get_pool",
    "init_db",
]
