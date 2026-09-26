"""/api/ask logic with fake retrieval and a fake LLM: no database, no network."""

from retrieval.pipeline import RetrievalResult

from app import answering
from app.answering import answer_question, build_answer, build_sources, detect_lang
from app.llm import LLMResult
from app.schemas import AskRequest

DECISION = {
    "chunk_id": "c1", "doc_id": "file:dgaurf.md/d.pdf", "kind": "file", "lang": "ro",
    "text": "5. Taxa este de 200 lei.\n6. Termenul este de 10 zile.",
    "title": "Cu privire la taxe", "doc_type": "decizie", "number": "12/14", "date": "2020-07-28",
    "citation_label": "Decizie nr. 12/14 din 2020-07-28 › pct. 5", "legal_path": ["pct. 5"],
    "url": "https://dgaurf.md/storage/d.pdf", "found_on": "https://dgaurf.md/ro/acte", "site": "dgaurf.md",
    "pages": [2], "has_contacts": False,
}
NEWER = DECISION | {
    "chunk_id": "c2", "number": "3/1", "date": "2024-01-10", "text": "Taxa este de 350 lei.",
    "citation_label": "Decizie nr. 3/1 din 2024-01-10", "legal_path": [], "url": "https://dgaurf.md/storage/n.pdf",
}
CONTACTS = {
    "chunk_id": "c3", "doc_id": "page:dgaurf.md/contacte", "kind": "page", "lang": "ro",
    "text": "Tel: 022 000 000", "title": "Contacte DGAURF", "url": "https://dgaurf.md/contacte",
    "found_on": "https://dgaurf.md/contacte", "site": "dgaurf.md", "has_contacts": True,
}
LINES = {
    "c1": [{"line_id": "l1", "idx": 0, "text": "5. Taxa este de 200 lei.", "page": 2},
           {"line_id": "l2", "idx": 1, "text": "6. Termenul este de 10 zile.", "page": 2}],
    "c2": [{"line_id": "l3", "idx": 0, "text": "Taxa este de 350 lei.", "page": 1}],
}


def llm_output(verdict="answered", sentences=(), missing=(), conflict=None, translations=()):
    return {"verdict": verdict, "sentences": list(sentences), "missing": list(missing),
            "conflict": conflict, "translations": list(translations)}


class FakeLLM:
    def __init__(self, data):
        self.data = data
        self.user_message = None

    def complete_json(self, system, user, schema_name, schema):
        self.user_message = user
        return LLMResult(data=self.data, model="fake", prompt_tokens=100, completion_tokens=20)


def ask(question, chunks, data, monkeypatch, tmp_path):
    monkeypatch.setattr(answering, "QUERY_LOG_DIR", tmp_path)
    llm = FakeLLM(data)
    response = answer_question(
        None, llm, AskRequest(question=question),
        retrieve_fn=lambda *a, **kw: RetrievalResult(items=chunks, not_found=not chunks),
        lines_fn=lambda pool, ids: {cid: LINES[cid] for cid in ids if cid in LINES},
        meta_fn=lambda pool, ids: {},
        next_fn=lambda pool, anchors: [],
    )
    return response, llm


def test_detect_lang():
    assert detect_lang("Cât costă autorizația?") == "ro"
    assert detect_lang("Сколько стоит разрешение?") == "ru"
    assert detect_lang("123 ?") is None


def test_answer_cites_lines_verbatim_from_index(monkeypatch, tmp_path):
    data = llm_output(sentences=[{"text": "Taxa este de 200 de lei.", "refs": ["S1.L1"]}])
    resp, llm = ask("Cât costă?", [DECISION], data, monkeypatch, tmp_path)

    assert "S1.L1: 5. Taxa este de 200 lei." in llm.user_message
    assert resp.status == "answered"
    assert resp.answer == "Taxa este de 200 de lei. [1]"
    c = resp.citations[0]
    assert c.passage == "5. Taxa este de 200 lei."  # from the index, not from the model
    assert (c.line_id, c.chunk_id, c.page, c.location) == ("l1", "c1", 2, "pct. 5")
    assert c.document_title == "Decizie nr. 12/14 din 2020-07-28"
    assert c.deep_link == "https://dgaurf.md/storage/d.pdf#page=2"
    assert resp.nav_links[0].url == "https://dgaurf.md/ro/acte"
    assert resp.query_id.startswith("q_")


def test_sentences_without_valid_refs_are_dropped(monkeypatch, tmp_path):
    data = llm_output(sentences=[
        {"text": "Taxa este de 200 lei.", "refs": ["S1.L1"]},
        {"text": "Se plătește la bancă.", "refs": []},       # no backing line
        {"text": "Termenul e de 3 zile.", "refs": ["S9.L1"]},  # invented ref
    ])
    resp, _ = ask("Cât costă?", [DECISION], data, monkeypatch, tmp_path)

    assert resp.answer == "Taxa este de 200 lei. [1]"
    assert len(resp.citations) == 1


def test_all_sentences_unbacked_means_not_found(monkeypatch, tmp_path):
    data = llm_output(sentences=[{"text": "Ceva inventat.", "refs": ["S1.L7"]}])
    resp, _ = ask("Cât costă?", [DECISION], data, monkeypatch, tmp_path)

    assert resp.status == "not_found"
    assert resp.citations == []


def test_not_found_points_to_contact_pages(monkeypatch, tmp_path):
    resp, _ = ask("Unde e piscina?", [DECISION, CONTACTS], llm_output("not_found"), monkeypatch, tmp_path)

    assert resp.status == "not_found"
    assert resp.answer == answering.NOT_FOUND["ro"]
    assert [link.url for link in resp.nav_links] == ["https://dgaurf.md/contacte"]


def test_empty_retrieval_skips_llm(monkeypatch, tmp_path):
    resp, llm = ask("Сколько стоит?", [], llm_output(), monkeypatch, tmp_path)

    assert resp.status == "not_found"
    assert resp.lang == "ru"
    assert llm.user_message is None


def test_out_of_scope(monkeypatch, tmp_path):
    resp, _ = ask("Ce vreme e mâine?", [DECISION], llm_output("out_of_scope"), monkeypatch, tmp_path)

    assert resp.status == "out_of_scope"
    assert resp.citations == []


def test_partial_answer_reports_gaps(monkeypatch, tmp_path):
    data = llm_output("partial", sentences=[{"text": "Taxa e 200 lei.", "refs": ["S1.L1"]}],
                      missing=["unde se depune cererea"])
    resp, _ = ask("Cât costă și unde depun?", [DECISION], data, monkeypatch, tmp_path)

    assert resp.status == "answered"
    assert resp.gaps == ["unde se depune cererea"]


def test_unclear_conflict_sets_status_and_cites_both_sides(monkeypatch, tmp_path):
    conflict = {"param": "taxa", "refs": ["S1.L1", "S2.L1"], "values": ["200 lei", "350 lei"],
                "resolution": "unclear"}
    data = llm_output(sentences=[{"text": "Documentele indică sume diferite.", "refs": ["S1.L1"]}],
                      conflict=conflict)
    resp, _ = ask("Cât costă?", [DECISION, NEWER], data, monkeypatch, tmp_path)

    assert resp.status == "conflict"
    assert resp.conflicts[0].citations == [1, 2]
    assert [c.line_id for c in resp.citations] == ["l1", "l3"]


def test_newer_act_is_not_a_conflict_status(monkeypatch, tmp_path):
    conflict = {"param": "taxa", "refs": ["S1.L1", "S2.L1"], "values": ["200 lei", "350 lei"],
                "resolution": "newer"}
    data = llm_output(sentences=[{"text": "Taxa actuală e 350 lei.", "refs": ["S2.L1"]}], conflict=conflict)
    resp, _ = ask("Cât costă?", [DECISION, NEWER], data, monkeypatch, tmp_path)

    assert resp.status == "answered"
    assert resp.conflicts[0].resolution == "newer"


def test_translation_attached_when_document_language_differs(monkeypatch, tmp_path):
    data = llm_output(sentences=[{"text": "Пошлина — 200 леев.", "refs": ["S1.L1"]}],
                      translations=[{"ref": "S1.L1", "text": "5. Пошлина составляет 200 леев."}])
    resp, _ = ask("Сколько стоит?", [DECISION], data, monkeypatch, tmp_path)

    assert resp.lang == "ru"
    assert resp.citations[0].passage == "5. Taxa este de 200 lei."
    assert resp.citations[0].passage_translation == "5. Пошлина составляет 200 леев."


def test_query_is_logged(monkeypatch, tmp_path):
    data = llm_output(sentences=[{"text": "Taxa e 200 lei.", "refs": ["S1.L1"]}])
    ask("Cât costă?", [DECISION], data, monkeypatch, tmp_path)

    [log_file] = tmp_path.glob("*.jsonl")
    record = log_file.read_text(encoding="utf-8")
    assert '"status": "answered"' in record and '"prompt_tokens": 100' in record


def test_citation_fields_missing_from_retrieval_are_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(answering, "QUERY_LOG_DIR", tmp_path)
    bare = {k: DECISION[k] for k in ("chunk_id", "doc_id", "kind", "lang", "text", "citation_label", "url", "site")}
    meta = {"c1": {k: DECISION[k] for k in ("title", "doc_type", "number", "date", "legal_path", "has_contacts")}}
    resp = answer_question(
        None, FakeLLM(llm_output(sentences=[{"text": "Taxa e 200 lei.", "refs": ["S1.L1"]}])),
        AskRequest(question="Cât costă?"),
        retrieve_fn=lambda *a, **kw: RetrievalResult(items=[bare]),
        lines_fn=lambda pool, ids: {"c1": LINES["c1"]},
        meta_fn=lambda pool, ids: meta,
        next_fn=lambda pool, anchors: [],
    )
    c = resp.citations[0]
    assert (c.document_title, c.location, c.published) == ("Decizie nr. 12/14 din 2020-07-28", "pct. 5", "2020-07-28")


def test_list_introduced_by_colon_pulls_the_next_chunk():
    intro = DECISION | {"chunk_id": "c1", "block_ids": [1, 2], "text": "Se constituie Grupul în următoarea componență:"}
    table = DECISION | {"chunk_id": "c9", "block_ids": [3], "anchor_pos": 1, "text": "| 1 | Dogotaru Svetlana |"}
    other = NEWER | {"block_ids": [0]}
    seen_anchors = []

    def next_fn(pool, anchors):
        seen_anchors.extend(anchors)
        return [table]

    chunks = answering.add_continuations(None, [intro, other], next_fn)
    assert [c["chunk_id"] for c in chunks] == ["c1", "c9", "c2"]
    assert seen_anchors == [(DECISION["doc_id"], 1)]


def test_chunk_without_line_index_falls_back_to_text_lines():
    [source] = build_sources([CONTACTS], {})
    assert [line["text"] for line in source.lines] == ["Tel: 022 000 000"]
    resp, _ = build_answer(llm_output(sentences=[{"text": "Tel.", "refs": ["S1.L1"]}]), [source], "ro", "q_1")
    assert resp.citations[0].passage == "Tel: 022 000 000"
    assert resp.citations[0].line_id is None
