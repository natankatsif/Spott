"""Admin gaps (docs/tasks/11 D): wording groups, the model's sorting (stored once), masking, hidden, re-check = exactly one model call."""

from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi.testclient import TestClient
from retrieval.pipeline import RetrievalResult

from app import answering, main
from app.gaps import gaps, group
from app.schemas import AskRequest, GapList
from app.wall import mask
from tests.test_admin import LOGIN
from tests.test_answering import DECISION, FakeLLM, FakeStore, model

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
TOPICS = {"garaj": [1.0, 0.0, 0.0], "гараж": [0.96, 0.28, 0.0], "piscin": [0.0, 0.0, 1.0]}


def embed(texts):
    """Paraphrases about a garage permit point the same way; the pool question elsewhere."""
    out = []
    for t in texts:
        key = next((k for k in TOPICS if k in t.lower()), "piscin")
        v = np.array(TOPICS[key])
        out.append(v / np.linalg.norm(v))
    return np.array(out)


def row(i, question, status="not_found", lang="ro", **extra):
    return {"answer_id": f"a{i}", "question": question, "lang": lang, "status": status,
            "created_at": T0 + timedelta(minutes=i), "missing": [], "retrieved_sites": ["dgaurf.md"],
            "gap_hidden": False, "recheck": None} | extra


ROWS = [
    row(1, "Cum obțin autorizație de construire pentru garaj?"),
    row(2, "Ce acte trebuie pentru autorizația de garaj?", status="partial", missing=["termenul de eliberare"]),
    row(3, "Как получить разрешение на строительство гаража?", lang="ru", retrieved_sites=["dgaurf.md", "acc.md"]),
    row(4, "Unde este piscina publică? Sunați-mă 069123456"),
]


def test_paraphrases_are_one_group_and_unrelated_is_its_own():
    groups = group(ROWS, embed)
    assert [[r["answer_id"] for r in g] for g in groups] == [["a1", "a2", "a3"], ["a4"]]
    result = gaps(ROWS, embed)
    first = result["items"][0]
    assert (first["id"], first["count"], first["status"], first["langs"]) == ("a1", 3, "not_found", ["ro", "ru"])
    assert first["missing"] == ["termenul de eliberare"]
    assert first["hint_sites"] == [{"site": "dgaurf.md", "hits": 3}, {"site": "acc.md", "hits": 1}]
    assert result["totals"] == {"not_found": 3, "partial": 1, "groups": 2}
    GapList.model_validate(result)


def test_questions_are_masked():
    item = gaps(ROWS, embed)["items"][1]
    assert item["example"] == "Unde este piscina publică? Sunați-mă •••"
    assert "069123456" not in str(item)


def test_hidden_and_solved_groups_are_left_out():
    rows = [r | {"gap_hidden": True} if r["answer_id"] == "a4" else r for r in ROWS]
    assert [i["id"] for i in gaps(rows, embed)["items"]] == ["a1"]
    assert [i["id"] for i in gaps(rows, embed, hidden=True)["items"]] == ["a1", "a4"]
    solved = [r | {"recheck": {"status": "answered", "verified": True, "answer_id": "a9", "ts": "…"}}
              if r["answer_id"] == "a1" else r for r in ROWS]
    assert [i["id"] for i in gaps(solved, embed)["items"]] == ["a4"]


def test_recheck_is_exactly_one_model_call(monkeypatch, tmp_path):
    """The admin's re-check runs the pipeline without the routing and rewrite calls and the second pass."""
    monkeypatch.setattr(answering, "QUERY_LOG_DIR", tmp_path)
    llm = FakeLLM(model(sentences=[{"refs": ["S1.L1"], "text": "Taxa e 200 lei."}]),
                  rewrite={"ro": "x", "ru": "x", "keywords": []})
    calls = []
    original = llm.complete_json
    llm.complete_json = lambda *a, **kw: calls.append(a[2]) or original(*a, **kw)
    r = answering.answer_question(FakeStore(), llm, AskRequest(question="Сколько стоит разрешение?"),
                                  retrieve_fn=lambda *a, **kw: RetrievalResult(items=[DECISION]),
                                  freshness=False, rewrite=False, routing=False)
    assert r.status == "answered" and len(llm.prompts) == 1 and calls == []


class FakeGaps:
    def __init__(self):
        self.asked = []

    def list(self, statuses, lang, days, limit, hidden):
        return gaps(ROWS, embed, hidden=hidden, limit=limit)

    def recheck(self, gap_id, answer):
        if gap_id != "a1":
            return None
        r = answer(AskRequest(question=ROWS[0]["question"], lang="ro"))
        return {"status": r.status, "verified": r.meta.verified, "answer_id": r.id, "ts": "2026-09-26T12:00:00+00:00"}

    def hide(self, gap_id, hidden):
        return gap_id == "a1"


def test_gap_endpoints(monkeypatch):
    monkeypatch.setenv("ADMIN_LOGIN", LOGIN["login"])
    monkeypatch.setenv("ADMIN_PASSWORD", LOGIN["password"])
    from app import admin
    admin.login_limiter.hits.clear()
    client = TestClient(main.app)
    main.app.state.gaps = FakeGaps()
    asked = []

    def ask_once(req):
        asked.append(req.question)
        return answering.AskResponse.model_validate_json(
            (main.Path(__file__).resolve().parents[2] / "frontend/src/lib/mocks/ask/answered-ro.json").read_text())

    main.app.state.ask_once = ask_once
    try:
        assert client.get("/api/admin/gaps").status_code == 401
        token = {"Authorization": "Bearer " + client.post("/api/admin/login", json=LOGIN).json()["token"]}
        body = client.get("/api/admin/gaps", headers=token).json()
        assert body["totals"]["groups"] == 2 and body["items"][0]["count"] == 3
        r = client.post("/api/admin/gaps/a1/recheck", headers=token)
        assert r.json()["status"] == "answered" and asked == [ROWS[0]["question"]]  # one question, once
        assert client.post("/api/admin/gaps/nope/recheck", headers=token).status_code == 404
        assert client.post("/api/admin/gaps/a1/hide", headers=token).json() == {"ok": True}
        assert client.post("/api/admin/gaps/a1/unhide", headers=token).json() == {"ok": True}
        assert client.get("/api/admin/gaps", headers=token, params={"status": "answered"}).status_code == 422
    finally:
        main.app.state.gaps = None


def test_the_model_joins_groups_one_document_would_answer_once():
    asked = []

    def cluster(new, existing):
        asked.append(([h["answer_id"] for h in new], existing))
        # the garage permit and the pool are different things; a stored decision is not asked again
        return {"group": {"a1": "a1"}, "topic": {"a1": "urbanism"},
                "title": {"a1": {"ro": "Taxa pentru autorizația de garaj", "ru": "Плата за разрешение на гараж"}}}

    rows = [r | {"answer": "Autorizația se eliberează de DGAURF."} for r in ROWS]
    rows[3] = rows[3] | {"topic": "culture", "gap_group": "a4"}  # sorted before
    result = gaps(rows, embed, cluster=cluster)
    assert asked == [(["a1"], [{"id": "a4", "topic": "culture", "title": mask(rows[3]["question"])}])]
    first, second = result["items"]
    assert (first["id"], first["topic"], first["count"]) == ("a1", "urbanism", 3)
    assert first["title"] == {"ro": "Taxa pentru autorizația de garaj", "ru": "Плата за разрешение на гараж"}
    assert (second["id"], second["topic"], second["title"]) == ("a4", "culture", None)
    assert result["topics"] == [{"topic": "urbanism", "groups": 1}, {"topic": "culture", "groups": 1}]
    assert first["last_answer"] == "Autorizația se eliberează de DGAURF."  # from the partial one
    assert second["last_answer"] is None  # never partly answered
    GapList.model_validate(result)


def test_wording_groups_the_model_put_together_are_one_group():
    rows = [r | {"gap_group": "a1"} for r in ROWS]  # the pool question sorted into the garage group (say)
    [item] = gaps(rows, embed)["items"]
    assert (item["id"], item["count"]) == ("a1", 4)


def test_question_embeddings_are_kept_between_lists():
    from app.gaps import CachedEmbed

    calls = []

    def counting(texts):
        calls.append(list(texts))
        return embed(texts)

    cached = CachedEmbed(counting)
    first = cached(["garaj a", "piscina b"])
    again = cached(["piscina b", "garaj a", "гараж c"])
    assert calls == [["garaj a", "piscina b"], ["гараж c"]]  # only the new question is embedded again
    assert np.allclose(again[1], first[0]) and len(again) == 3
