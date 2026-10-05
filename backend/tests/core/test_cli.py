"""qsearch: command parsing, log round-trip and the tester report."""

from spott.core.cli import append_log, parse_args, parse_command, read_logs, resolve_doc, result_summary, summarize


def test_parse_command():
    assert parse_command("что такое RAS?") == ("search", "что такое RAS?")
    assert parse_command(":ok 2") == ("ok", "2")
    assert parse_command(":note  нет даты ") == ("note", "нет даты")
    assert parse_command(":Q") == ("q", "")


def test_resolve_doc_by_number_and_id():
    results = [{"doc_id": "file:a", "chunk_id": "c1"}, {"doc_id": "page:b", "chunk_id": "c2"}]
    assert resolve_doc("2", results) == ("page:b", "c2")
    assert resolve_doc("3", results) == (None, None)
    assert resolve_doc("file:x", results) == ("file:x", None)


def test_result_summary_keeps_line_ids():
    item = {"chunk_id": "c", "doc_id": "d", "lang": "ro", "score": 0.123456, "matched_lines": [{"line_id": "l1"}]}
    s = result_summary(1, item)
    assert s["line_ids"] == ["l1"] and s["score"] == 0.1235


def test_log_roundtrip_is_utf8(tmp_path):
    append_log({"type": "query", "query_id": "q1", "query": "Cât costă? Сколько стоит?"}, tmp_path)
    assert read_logs(tmp_path)[0]["query"] == "Cât costă? Сколько стоит?"


def test_summarize_last_mark_wins_and_notes_attach():
    entries = [
        {"type": "query", "query_id": "q1", "query": "a"},
        {"type": "query", "query_id": "q2", "query": "b"},
        {"type": "query", "query_id": "q3", "query": "c"},
        {"type": "bad", "query_id": "q1"},
        {"type": "ok", "query_id": "q1", "rank": 3},
        {"type": "bad", "query_id": "q2"},
        {"type": "note", "query_id": "q2", "note": "ответ в PDF, стр. 4"},
        {"type": "ok", "query_id": "unknown", "rank": 1},
    ]
    s = summarize(entries)
    assert (s["queries"], s["marked"], s["unmarked"]) == (3, 2, 1)
    assert (s["ok"], s["bad"], s["none"]) == (1, 1, 0)
    assert s["avg_ok_rank"] == 3
    assert s["bad_list"] == [{"query": "b", "notes": ["ответ в PDF, стр. 4"]}]


def test_summarize_empty():
    assert summarize([])["queries"] == 0


def test_parse_args_defaults():
    a = parse_args([])
    assert (a.query, a.lang, a.k, a.report) == (None, "all", 5, False)
