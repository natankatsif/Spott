"""Every API test starts from the app as imported: no logs in the real data/, no login attempts or app.state left
over from another test. Here and not in tests/conftest.py, so that the core and ingest tests don't import the API."""

import pytest

from spott.api import admin, main
from spott.api.answering import pipeline


@pytest.fixture(autouse=True)
def log_dirs(monkeypatch, tmp_path):
    """Every answer, greetings included, is appended to a log under data/."""
    monkeypatch.setattr(pipeline, "QUERY_LOG_DIR", tmp_path / "query_logs")


@pytest.fixture(autouse=True)
def login_attempts():
    """Logins are rate limited per client, and every TestClient is the same client."""
    admin.login_limiter.hits.clear()


@pytest.fixture(autouse=True)
def app_state():
    """The tests put their fakes on main.app.state instead of running the lifespan; none survives the test."""
    state = main.app.state
    saved = {key: state[key] for key in state}
    yield
    for key in list(state):
        del state[key]
    for key, value in saved.items():
        state[key] = value
