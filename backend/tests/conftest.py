"""Fixtures for every test: nothing a test writes lands in the real data/ directory or the real tables."""

import os
import uuid

import psycopg
import pytest

from spott.core.db import get_connection


@pytest.fixture(autouse=True)
def qsearch_log_dir(monkeypatch, tmp_path):
    """qsearch keeps the tester's marks in data/test_logs unless QSEARCH_LOG_DIR says otherwise."""
    monkeypatch.setenv("QSEARCH_LOG_DIR", str(tmp_path / "test_logs"))


@pytest.fixture
def pg():
    """An autocommit connection to the configured Postgres (POSTGRES_* as in .env) whose tables go to a schema of
    its own, dropped after the test. No Postgres: the test is skipped, or fails with SPOTT_TEST_PG=require (CI)."""
    try:
        conn = get_connection(autocommit=True, register=False)
    except psycopg.OperationalError as e:
        if os.getenv("SPOTT_TEST_PG") == "require":
            raise
        pytest.skip(f"no Postgres: {e}")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}")
    try:
        yield conn
    finally:
        conn.close()
        with get_connection(autocommit=True, register=False) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
