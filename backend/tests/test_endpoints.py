"""HTTP layer of docs/API.md without models or a database: error bodies, wall, rate limit, feedback."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.errors import ApiException, RateLimiter
from app.schemas import AnswerMeta, AskRequest, AskResponse
from app.wall import Wall, mask


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "FEEDBACK_DIR", tmp_path / "feedback")
    main.app.state.wall = Wall()
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


def test_feedback_is_stored(client, tmp_path):
    r = client.post("/api/feedback", json={"answer_id": "a1", "vote": "down", "comment": "sursa e veche",
                                           "citation_id": "c2"})
    assert r.json() == {"ok": True}
    [log] = (tmp_path / "feedback").glob("*.jsonl")
    assert json.loads(log.read_text(encoding="utf-8"))["vote"] == "down"


def test_feedback_rejects_unknown_vote(client):
    assert client.post("/api/feedback", json={"answer_id": "a1", "vote": "maybe"}).status_code == 422


def test_wall_returns_newest_first_and_only_newer_than_after(client):
    wall = main.app.state.wall
    for i, status in enumerate(["answered", "not_found", "refused"], 1):
        wall.add(AskRequest(question=f"Întrebarea {i}"), response(status, f"a{i}"))

    body = client.get("/api/wall").json()
    assert [i["id"] for i in body["items"]] == ["a3", "a2", "a1"]
    assert body["total_questions"] == 3 and body["by_status"]["not_found"] == 1
    assert [i["id"] for i in client.get("/api/wall?after=a1").json()["items"]] == ["a3", "a2"]


def test_wall_masks_personal_data():
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
    try:
        r = client.post("/api/feedback", json={"answer_id": "a1", "rating": 2, "tags": ["outdated", "wrong_source"],
                                               "comment": "sursa e veche", "session_id": "s1"})
        assert r.json() == {"ok": True}
        client.post("/api/feedback", json={"answer_id": "a1", "vote": "up"})  # older clients: up = 5 stars
        assert ratings.saved == [("a1", 2, ["outdated", "wrong_source"], "s1"), ("a1", 5, [], None)]
        assert client.post("/api/feedback", json={"answer_id": "nope", "rating": 5}).status_code == 404
        for bad in ({"answer_id": "a1"}, {"answer_id": "a1", "rating": 6}, {"answer_id": "a1", "rating": 3, "tags": ["meh"]}):
            assert client.post("/api/feedback", json=bad).status_code == 422
    finally:
        main.app.state.answers = None


class Visitors:
    def __init__(self):
        self.seen = set()

    def visit(self, visitor_id):
        self.seen.add(visitor_id)
        return len(self.seen)


def test_visits_count_each_browser_once(client):
    main.app.state.visitors = Visitors()
    try:
        assert client.post("/api/visits", json={"visitor_id": "anon-1"}).json() == {"visitors": 1}
        assert client.post("/api/visits", json={"visitor_id": "anon-1"}).json() == {"visitors": 1}
        assert client.post("/api/visits", json={"visitor_id": "anon-2"}).json() == {"visitors": 2}
        assert client.post("/api/visits", json={"visitor_id": ""}).status_code == 422
    finally:
        main.app.state.visitors = None
    r = client.post("/api/visits", json={"visitor_id": "anon-3"})
    assert (r.status_code, r.json()["error"]) == (503, "unavailable")
