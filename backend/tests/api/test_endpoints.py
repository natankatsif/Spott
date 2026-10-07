"""HTTP layer of docs/API.md without models or a database: error bodies, the checks before an answer,
masking of personal data, rate limit, feedback."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from spott.api import main
from spott.api.errors import ApiException, RateLimiter
from spott.api.llm import LLMUnavailable
from spott.api.masking import mask
from spott.api.schemas import AnswerMeta, AskResponse


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "FEEDBACK_DIR", tmp_path / "feedback")
    main.app.state.rate_limiter = RateLimiter(limit=2)
    main.app.state.pool = None
    main.app.state.store = None
    # Not entered as a context manager: that would run the lifespan (models, database).
    return TestClient(main.app, raise_server_exceptions=False)


def response(status="answered", answer_id="a1"):
    return AskResponse(id=answer_id, status=status, lang="ro", answer="Text.", sentences=[], citations=[],
                       conflict=None, checklist=None, nav_links=[], followups=[], trace=[],
                       meta=AnswerMeta(model="m", path="fast", latency_ms=1200, verified=True))


def test_validation_error_has_api_error_body(client):
    r = client.post("/api/ask", json={"question": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error" and body["retry_after_s"] is None
    assert "question" in body["message"]


def test_unavailable_database_is_503_api_error(client):
    r = client.post("/api/ask", json={"question": "Cât costă?"})
    assert (r.status_code, r.json()["error"]) == (503, "unavailable")


def test_unknown_document_file_is_404_api_error(client):
    r = client.get("/api/documents/file%3Adgaurf.md%2Fstorage%2Fx.pdf/file")
    assert (r.status_code, r.json()["error"]) == (404, "not_found")


def on_loop() -> bool:
    """Whether this runs in the event loop's own thread, where a blocking call holds up every request."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class Holder:
    """get_llm()'s LLMHolder, which re-reads the admin's model settings from Postgres: notes where it is asked."""

    def __init__(self, llm=None):
        self.llm, self.on_loop = llm, []

    def get(self):
        self.on_loop.append(on_loop())
        if self.llm is None:
            raise LLMUnavailable("no API key")
        return self.llm


class Cache:
    """The quick questions: every question has a checked answer, replayed without a model call."""

    def cached(self, req):
        return response(answer_id="a_cached")


class Recorded:
    def __init__(self):
        self.on_loop = []

    def record(self, req, resp, info=None):
        self.on_loop.append(on_loop())


@pytest.fixture
def ready(client):
    main.app.state.pool = object()  # not None: never queried with a cached answer or without a model
    main.app.state.models_loaded = True
    return client


def test_the_model_settings_are_read_off_the_event_loop(ready):
    main.app.state.llm_holder = holder = Holder()
    for path in ("/api/ask", "/api/ask/stream"):
        r = ready.post(path, json={"question": "Cât costă?"})
        assert (r.status_code, r.json()["message"]) == (503, "LLM not configured: no API key")
    assert holder.on_loop == [False, False]


def test_a_cached_answer_is_replayed_and_recorded_off_the_event_loop(ready):
    main.app.state.llm_holder = Holder(llm=object())
    main.app.state.suggestions, main.app.state.answers = Cache(), (answers := Recorded())
    r = ready.post("/api/ask", json={"question": "Cât costă?"})
    assert r.status_code == 200 and r.json()["meta"]["path"] == "cache" and r.json()["id"] != "a_cached"
    assert answers.on_loop == [False]


def test_the_checks_before_an_answer_keep_their_order(ready):
    """422 (the body), then 503 (no database, warming up, no model), then 429: a question that can't be answered
    doesn't use up the client's limit."""
    main.app.state.llm_holder = holder = Holder()
    main.app.state.suggestions = Cache()

    def ask(question="Cât costă?"):  # a 503 by its message: there are three
        r = ready.post("/api/ask", json={"question": question})
        return r.json()["message"] if r.status_code == 503 else r.status_code

    assert [ask() for _ in range(3)] == ["LLM not configured: no API key"] * 3  # the limit is 2
    holder.llm = object()
    assert [ask() for _ in range(3)] == [200, 200, 429]
    main.app.state.models_loaded = False
    assert ask() == "Service is warming up, try again in a few seconds"
    assert len(holder.on_loop) == 6  # not asked for a model while warming up
    main.app.state.pool = None
    assert ask() == "Database pool not initialized"
    assert ask("") == 422


def test_feedback_is_stored(client, tmp_path):
    r = client.post("/api/feedback", json={"answer_id": "a1", "vote": "down", "comment": "sursa e veche"})
    assert r.json() == {"ok": True}
    [log] = (tmp_path / "feedback").glob("*.jsonl")
    assert json.loads(log.read_text(encoding="utf-8"))["vote"] == "down"


def test_feedback_rejects_unknown_vote(client):
    assert client.post("/api/feedback", json={"answer_id": "a1", "vote": "maybe"}).status_code == 422


def test_personal_data_is_masked():
    assert mask("Sunați-mă la 069 123 456 sau ion.popescu@mail.md") == "Sunați-mă la ••• sau •••"
    assert mask("IDNP 2002001234567, dosarul") == "IDNP •••, dosarul"
    assert mask("Decizia nr. 12/14 din 2020") == "Decizia nr. 12/14 din 2020"
    assert len(mask("x" * 500)) == 200


def test_rate_limiter():
    limiter = RateLimiter(limit=2, window_s=60)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    limiter.check("5.6.7.8")
    with pytest.raises(ApiException) as e:
        limiter.check("1.2.3.4")
    assert (e.value.status, e.value.code) == (429, "rate_limited")
    assert 0 < e.value.retry_after_s <= 60


class Ratings:
    def __init__(self):
        self.saved = []

    def rate(self, req):
        if req.answer_id != "a1":
            return False
        self.saved.append((req.answer_id, req.stars, req.tags, req.session_id))
        return True


def test_star_rating_with_tags_is_stored(client):
    main.app.state.answers = ratings = Ratings()
    r = client.post("/api/feedback", json={"answer_id": "a1", "rating": 2, "tags": ["outdated", "wrong_source"],
                                           "comment": "sursa e veche", "session_id": "s1"})
    assert r.json() == {"ok": True}
    client.post("/api/feedback", json={"answer_id": "a1", "vote": "up"})  # older clients: up = 5 stars
    assert ratings.saved == [("a1", 2, ["outdated", "wrong_source"], "s1"), ("a1", 5, [], None)]
    assert client.post("/api/feedback", json={"answer_id": "nope", "rating": 5}).status_code == 404
    for bad in ({"answer_id": "a1"}, {"answer_id": "a1", "rating": 6}, {"answer_id": "a1", "rating": 3, "tags": ["meh"]}):
        assert client.post("/api/feedback", json=bad).status_code == 422


class Visitors:
    def __init__(self):
        self.seen = set()

    def visit(self, visitor_id):
        self.seen.add(visitor_id)
        return len(self.seen)


def test_visits_count_each_browser_once(client):
    main.app.state.visitors = Visitors()
    assert client.post("/api/visits", json={"visitor_id": "anon-1"}).json() == {"visitors": 1}
    assert client.post("/api/visits", json={"visitor_id": "anon-1"}).json() == {"visitors": 1}
    assert client.post("/api/visits", json={"visitor_id": "anon-2"}).json() == {"visitors": 2}
    assert client.post("/api/visits", json={"visitor_id": ""}).status_code == 422
    main.app.state.visitors = None
    r = client.post("/api/visits", json={"visitor_id": "anon-3"})
    assert (r.status_code, r.json()["error"]) == (503, "unavailable")
