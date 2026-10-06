"""Quick questions (docs/tasks/09 §3): the rules, merged wordings, replay of a checked answer — no database."""

import json

import numpy as np
from fastapi.testclient import TestClient

from app import main
from app.answering import replay_events
from app.schemas import AskRequest, AskResponse, Suggestion
from app.suggestions import choose, good_answer, qualifies
from tests.test_contract import MOCKS


def row(question, lang="ro", asked=3, good=True, avg=None, low=None):
    return {"question": question, "lang": lang, "asked_count": asked, "always_good": good, "rating_avg": avg,
            "rating_min": low, "answer_id": f"a-{question[:5]}"}


def embed(texts):
    """Same vector for wordings of the same question (they share their first word here)."""
    vectors = {"cine": [1.0, 0.0, 0.0], "unde": [0.0, 1.0, 0.0]}
    return np.array([vectors.get(t.split()[0].lower(), [0.0, 0.0, 1.0]) for t in texts])


def test_rules_of_a_quick_question():
    rows = [
        row("Cine elaborează Planul urbanistic general?", asked=5),
        row("Cine face Planul urbanistic general?", asked=2),  # the same question: joins the first
        row("Unde se află Parcul urban de autobuze?", asked=1),  # asked once
        row("Ce taxe sunt pentru certificatul de urbanism?", good=False),  # once not answered well
        row("Cât costă abonamentul la transport?", avg=4.5, low=2),  # someone rated it 2
        row("Ce program are DGAURF sâmbăta?", avg=3.5, low=3),  # average under 4
        row("Sunați-mă la 069123456 despre PUG", asked=4),  # personal data
        row("PUG?", asked=9),  # too short
        row("Кто разрабатывает генплан Кишинёва?", lang="ru", asked=2, avg=5.0, low=5),
    ]
    chosen = choose(rows, embed)
    assert [(r["question"], r["asked_count"]) for r in chosen] == [
        ("Cine elaborează Planul urbanistic general?", 7), ("Кто разрабатывает генплан Кишинёва?", 2)]
    assert qualifies("Cine elaborează PUG?") and not qualifies("x" * 121)


def test_checked_answer_is_replayed_as_a_stream():
    cached = AskResponse.model_validate(json.loads((MOCKS / "ask" / "answered-ro.json").read_text(encoding="utf-8")))
    assert good_answer(cached)
    done = []
    events = list(replay_events(cached, AskRequest(question="x"), on_done=lambda req, r, info: done.append(r)))
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "done" and "delta" in types
    r = AskResponse.model_validate(events[-1]["response"])
    assert r.meta.path == "cache" and r.id != cached.id and r.sentences == cached.sentences
    assert done[0].id == r.id


class FakeSuggestions:
    def list(self, lang, limit):
        return [Suggestion(id=1, question="Cine elaborează Planul urbanistic general?", lang="ru" if lang == "ru" else "ro", answer_id="a1",
                           asked_count=7, rating_avg=None, pinned=False)][:limit]


def test_suggestions_endpoint():
    main.app.state.suggestions = FakeSuggestions()
    try:
        client = TestClient(main.app)
        r = client.get("/api/suggestions", params={"lang": "ro", "limit": 6})
        assert r.json()["items"][0]["asked_count"] == 7
        assert client.get("/api/suggestions", params={"lang": "en"}).status_code == 200  # the RO questions, in English
        assert client.get("/api/suggestions", params={"lang": "de"}).status_code == 422
    finally:
        main.app.state.suggestions = None
