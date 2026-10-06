"""Golden streams of /api/ask/stream (docs/API.md): every event answer_events() and replay_events() yield for each
kind of answer, compared with tests/api/golden/<scenario>.json. No schema describes the events, so this is where an
event that goes missing, appears or moves, or a renamed type or payload key, fails a test.

What changes from run to run is replaced before comparing: the answer id by "<answer id>", the timings (a trace
step's ms, meta.latency_ms) by "<ms>". Time to first token and timestamps go only to the query log, not to events.

A change to the events that is meant: rewrite the files, then review their diff.

    UPDATE_GOLDEN=1 uv run --no-sync pytest tests/api/test_sse_golden.py
"""

import json
import os
import re
from pathlib import Path

import pytest
from tests.api.fakes import CONTACTS, DECISION, DGMU, NEWER, FakeLLM, FakeStore, model, s

from spott.api.answering import answer_events, replay_events
from spott.api.schemas import AskRequest, AskResponse
from spott.core.pipeline import RetrievalResult

GOLDEN = Path(__file__).parent / "golden"
ANSWER_ID = re.compile(r"a_[0-9a-f]{16}")
TIMINGS = {"ms", "latency_ms"}


def ask(question, chunks, llm, store=None, retrieve_fn=None, freshness=False, routing=False):
    # Freshness, rewrite and routing are given, not read from the environment, so the streams don't depend on it.
    return list(answer_events(
        store or FakeStore(), llm, AskRequest(question=question),
        retrieve_fn=retrieve_fn or (lambda *a, **kw: RetrievalResult(items=chunks, not_found=not chunks)),
        freshness=freshness, rewrite=False, routing=routing,
    ))


def answered():
    # The sentences stream while the model writes them; the checklist's deadline cites a line no sentence cites, so
    # that citation comes after the last sentence.
    checklist = {"title": "Cum obțin certificatul", "steps": [s("Depuneți cererea.", "S1.L1")], "documents_needed": [],
                 "fee": s("200 lei", "S1.L1"), "deadline": s("10 zile", "S1.L2")}
    data = model(sentences=[s("Taxa este de 200 lei.", "S1.L1"), s("Cererea se depune la DGAURF.", "S1.L1")],
                 checklist=checklist, followups=["Care este termenul?"])
    return ask("Cum obțin certificatul?", [DECISION], FakeLLM(data))


def not_found():
    # The model finds no answer in the sources: the fixed text, and a link to the contacts page it was given.
    return ask("Unde e piscina?", [DECISION, CONTACTS], FakeLLM(model("not_found")))


def contacts():
    # The search finds nothing, so the model isn't asked: the nearest contact card of the corpus is named instead.
    return ask("Кто отвечает за парковки?", [], FakeLLM(model()), store=FakeStore(contacts=[DGMU]))


def partial():
    # A Russian answer to part of the question: the model's sentence streams, the missing part and the contact are
    # added after it, and the quote's translation comes only with done.
    data = model("partial", sentences=[s("Пошлина — 200 леев.", "S1.L1")],
                 missing=["Где подать заявление, в документах не указано."],
                 translations=[{"ref": "S1.L1", "text": "5. Пошлина составляет 200 леев."}])
    return ask("Сколько стоит и где подать заявление?", [DECISION], FakeLLM(data), store=FakeStore(contacts=[DGMU]))


def second_pass():
    # The first answer is partial and already streamed; the model's own query then finds a newer act, and the second
    # answer over it arrives only with done.
    def retrieve(pool, query, **kw):
        return RetrievalResult(items=[NEWER] if query == "taxa actuală" else [DECISION])

    first = model("partial", sentences=[s("Taxa este de 200 lei.", "S1.L1")],
                  missing=["Taxa actuală nu este indicată."], search_ro="taxa actuală")
    second = model(sentences=[s("Taxa actuală este de 350 lei.", "S1.L1"), s("Anterior era de 200 lei.", "S2.L1")])
    return ask("Cât costă certificatul?", [], FakeLLM(first, second), store=FakeStore(meta={"c2": NEWER}),
               retrieve_fn=retrieve, freshness=True)


def chat():
    # The routing model answers small talk itself and suggests questions; nothing is searched.
    llm = FakeLLM(model())
    llm.route = {"route": "chat", "reply": "Спасибо, всё хорошо! Чем помочь по Примэрии?",
                 "options": ["Как получить справку?", "Сколько стоит разрешение на строительство?"]}
    return ask("Как дела?", [DECISION], llm, routing=True)


def small_talk():
    # Without the routing model a greeting is still answered by code, not "not found".
    return ask("Bună ziua", [DECISION], FakeLLM(model()))


def replay():
    # A quick question's checked answer from the cache, as /api/ask/stream replays it: a new id, meta.path "cache".
    fixture = Path(__file__).parent / "fixtures" / "answered-ro.json"
    cached = AskResponse.model_validate_json(fixture.read_text(encoding="utf-8"))
    return list(replay_events(cached, AskRequest(question="Cine elaborează Planul urbanistic general?")))


SCENARIOS = {f.__name__: f for f in (answered, not_found, contacts, partial, second_pass, chat, small_talk, replay)}


def normalized(events: list[dict]) -> list[dict]:
    """The events with placeholders for the answer id and the timings. Only the id the start event announced is
    replaced, so a done that carries another id still differs from the golden file."""
    first = events[0].get("id") if events else None
    answer_id = first if isinstance(first, str) and ANSWER_ID.fullmatch(first) else None

    def walk(value, key=None):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if answer_id is not None and value == answer_id:
            return "<answer id>"
        if key in TIMINGS and isinstance(value, int | float) and not isinstance(value, bool):
            return "<ms>"
        return value

    return walk(events)


@pytest.mark.parametrize("name", SCENARIOS)
def test_stream_matches_the_golden_file(name):
    events = normalized(json.loads(json.dumps(SCENARIOS[name]())))  # what the client parses from the stream
    path = GOLDEN / f"{name}.json"
    if os.getenv("UPDATE_GOLDEN") == "1":
        path.write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert path.exists(), f"no {path.name}: write it with UPDATE_GOLDEN=1"
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert [e.get("type") for e in events] == [e.get("type") for e in golden], "an event is missing, added or moved"
    assert events == golden
