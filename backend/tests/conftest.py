"""Fixtures for every test: nothing a test writes lands in the real data/ directory."""

import pytest


@pytest.fixture(autouse=True)
def qsearch_log_dir(monkeypatch, tmp_path):
    """qsearch keeps the tester's marks in data/test_logs unless QSEARCH_LOG_DIR says otherwise."""
    monkeypatch.setenv("QSEARCH_LOG_DIR", str(tmp_path / "test_logs"))
