"""The incremental JSON reader behind real streaming: same events however the text is cut into pieces."""

import json

import pytest

from spott.api.jsonstream import JsonEvents

DOC = {"verdict": "answered",
       "sentences": [{"refs": ["S1.L1", "S2.L3"], "text": "Taxa e \"200\" lei — ș\n😀."}, {"refs": [], "text": ""}],
       "conflict": None, "n": 12.5, "ok": True, "empty": []}


def events(text: str, step: int) -> list[tuple]:
    reader, out = JsonEvents(), []
    for i in range(0, len(text), step):
        out += reader.feed(text[i:i + step])
    return out


@pytest.mark.parametrize("text", [json.dumps(DOC), json.dumps(DOC, ensure_ascii=False, indent=2)])
@pytest.mark.parametrize("step", [1, 2, 5, 10_000])
def test_same_events_for_any_split(text, step):
    evs = events(text, step)
    streamed = "".join(e[2] for e in evs if e[0] == "text" and e[1] == ("sentences", 0, "text"))
    assert streamed == DOC["sentences"][0]["text"]  # escapes, \n and a surrogate pair split anywhere
    values = {e[1]: e[2] for e in evs if e[0] == "value"}
    assert values[("verdict",)] == "answered"
    assert values[("sentences", 0, "refs", 1)] == "S2.L3"
    assert (values[("conflict",)], values[("n",)], values[("ok",)]) == (None, 12.5, True)
    assert [e[1] for e in evs if e[0] == "end"] == [
        ("sentences", 0, "refs"), ("sentences", 0), ("sentences", 1, "refs"), ("sentences", 1), ("sentences",),
        ("empty",), ()]


def test_text_arrives_before_the_string_closes():
    reader = JsonEvents()
    assert reader.feed('{"sentences":[{"refs":["S1.L1"],"text":"Taxa ') == [
        ("text", ("sentences", 0, "refs", 0), "S1.L1"), ("value", ("sentences", 0, "refs", 0), "S1.L1"),
        ("end", ("sentences", 0, "refs")), ("text", ("sentences", 0, "text"), "Taxa ")]
    assert reader.feed('e 200 lei."}') == [
        ("text", ("sentences", 0, "text"), "e 200 lei."), ("value", ("sentences", 0, "text"), "Taxa e 200 lei."),
        ("end", ("sentences", 0))]
