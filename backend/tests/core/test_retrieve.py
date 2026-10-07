"""retrieve() as it is today, with its index queries, the embedding model and the clock patched out.

A characterization test: it pins what retrieve() returns (the fused ranking, the chunk rows of line hits, matched
lines, timings, not_found) so the function can be taken apart without changing its results. Once the index queries
come in as an argument, tests on a fake index replace this module.
"""

import contextlib
import copy
import threading

import numpy as np
import pytest
from psycopg_pool import ConnectionPool

from spott.core import pipeline
from spott.core.pipeline import NOT_FOUND_THRESHOLD, retrieve
from spott.core.search import build_fts_query
from spott.core.tools import search_tool

QUERY = "autorizație de construire"
WEIGHTS = {"w_vector": 1.0, "w_fts": 0.1, "w_line": 1.0}  # given, so RRF_W_* in the environment can't move them
# Seconds each step takes on the fake clock.
DURATIONS = {"embed": 0.004, "chunk_vector": 0.010, "line_vector": 0.030, "chunk_fts": 0.002, "line_fts": 0.005,
             "rerank": 0.020}
QUERIES = {"execute_vector_query": "chunk_vector", "execute_line_vector_query": "line_vector",
           "execute_fts_query": "chunk_fts", "execute_line_fts_query": "line_fts"}


PAGE = {"kind": "page", "doc_type": None, "url": "https://acc.md/ro/servicii", "pages": None, "found_on": None}


def chunk(cid: str, **fields) -> dict:
    """A chunks row (search.RESULT_COLUMNS), as get_chunks_by_ids returns it."""
    return {"chunk_id": cid, "doc_id": f"d_{cid}", "site": "acc.md", "kind": "file", "doc_type": "decizie",
            "lang": "ro", "url": f"https://acc.md/{cid}.pdf", "citation_label": f"Act {cid}", "text": f"text of {cid}",
            "embed_text": f"text of {cid}", "content_hash": f"h_{cid}", "parent_legal_path": None, "pages": [2],
            "found_on": "https://acc.md/acte"} | fields


def hit(cid: str, score: float, **fields) -> dict:
    """A row of the chunk vector or chunk FTS query."""
    return chunk(cid, **fields) | {"score": score, "rrf_score": score}


def line(lid: str, cid: str, idx: int, score: float) -> dict:
    """A row of the line vector or line FTS query (search.LINE_RESULT_COLUMNS). Its page is never the chunk's."""
    return {"line_id": lid, "chunk_id": cid, "doc_id": f"d_{cid}", "idx": idx, "text": f"line {lid}",
            "embed_text": f"line {lid}", "lang": "ro", "block_id": 1, "page": 7, "bboxes": [{"page": 7}],
            "score": score}


class Clock:
    """time.perf_counter for retrieve(). Every thread keeps its own time, which only the fakes move, so a query's
    duration comes out exact even while the four run at once."""

    def __init__(self):
        self.local = threading.local()

    def perf_counter(self) -> float:
        return getattr(self.local, "now", 0.0)

    def advance(self, step: str) -> None:
        self.local.now = self.perf_counter() + DURATIONS[step]


class Model:
    def __init__(self, clock: Clock):
        self.clock, self.calls = clock, []

    def encode(self, texts, normalize_embeddings):
        self.calls.append((texts, normalize_embeddings))
        self.clock.advance("embed")
        return np.array([[0.6, 0.8]])


class Index:
    """What the four index queries, get_chunks_by_ids and the first-lines query of retrieve() find."""

    def __init__(self):
        self.results: dict[str, list[dict]] = {name: [] for name in QUERIES.values()}
        self.chunks: dict[str, dict] = {}
        self.first_lines: list[dict] = []
        self.clock = Clock()
        self.model = Model(self.clock)
        self.conn = Conn(self)
        self.calls: dict[str, list] = {name: [] for name in QUERIES.values()}
        self.chunk_requests: list[list[str]] = []
        self.first_line_requests: list[list[str]] = []
        self.barrier: threading.Barrier | None = None

    def query(self, name: str):
        def run(conn, q, **filters):
            assert conn is self.conn
            self.calls[name].append((q, filters))
            if self.barrier:
                self.barrier.wait(timeout=5)
            self.clock.advance(name)
            return copy.deepcopy(self.results[name])

        return run

    def chunks_by_ids(self, conn, ids):
        assert conn is self.conn
        self.chunk_requests.append(ids)
        return {cid: copy.deepcopy(self.chunks[cid]) for cid in ids if cid in self.chunks}


class Conn:
    """The connection retrieve() reads the first lines of a chunk with; the other queries are patched."""

    def __init__(self, index: Index):
        self.index = index

    def cursor(self, row_factory=None):
        return Cursor(self.index)


class Cursor:
    def __init__(self, index: Index):
        self.index, self.rows = index, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        assert "FROM lines" in sql and "idx < 3" in sql
        (ids,) = params
        self.index.first_line_requests.append(ids)
        self.rows = [copy.deepcopy(r) for r in self.index.first_lines if r["chunk_id"] in ids]

    def fetchall(self):
        return self.rows


class Pool(ConnectionPool):
    """A pool is what makes retrieve() run its four queries in parallel; this one never connects."""

    def __init__(self, conn: Conn):
        super().__init__(open=False)
        self.conn = conn

    @contextlib.contextmanager
    def connection(self, timeout=None):
        yield self.conn


@pytest.fixture
def index(monkeypatch):
    found = Index()
    for function, name in QUERIES.items():
        monkeypatch.setattr(pipeline, function, found.query(name))
    monkeypatch.setattr(pipeline, "get_chunks_by_ids", found.chunks_by_ids)
    monkeypatch.setattr(pipeline, "get_embedding_model", lambda device: found.model)
    monkeypatch.setattr(pipeline, "get_device", lambda: "test-device")
    monkeypatch.setattr(pipeline, "time", found.clock)
    return found


@pytest.fixture(params=["pool", "connection"])
def db(request, index):
    """The API and qsearch call retrieve() with a pool, eval_smoke with one connection."""
    return Pool(index.conn) if request.param == "pool" else index.conn


def every_way(index: Index) -> None:
    """c1: chunk vector and line vector. c2, a page: chunk vector and chunk FTS. c3: line vector only. c4: chunk FTS
    only, no lines matched. c5: line FTS only."""
    index.results["chunk_vector"] = [hit("c1", 0.9), hit("c2", 0.8, **PAGE)]
    index.results["line_vector"] = [line("l3", "c3", 0, 0.85), line("l1a", "c1", 0, 0.7), line("l1b", "c1", 4, 0.6),
                                    line("l1d", "c1", 5, 0.5)]
    index.results["chunk_fts"] = [hit("c2", 0.5, **PAGE), hit("c4", 0.3)]
    index.results["line_fts"] = [line("l1c", "c1", 3, 0.65), line("l1a", "c1", 0, 0.2), line("l2", "c2", 1, 0.3),
                                 line("l5", "c5", 0, 0.9)]
    index.chunks = {cid: chunk(cid) for cid in ("c1", "c3", "c4", "c5")}
    index.first_lines = [{"line_id": "l4a", "chunk_id": "c4", "idx": 0, "text": "line l4a", "page": 5,
                          "bboxes": [{"page": 5}]},
                         {"line_id": "l4b", "chunk_id": "c4", "idx": 1, "text": "line l4b", "page": None,
                          "bboxes": None}]


def matched(lid: str, cid: str, idx: int, score: float, **fields) -> dict:
    """A matched line of a pdf chunk: the chunk's address, and its first page, not the line's own."""
    return {"line_id": lid, "idx": idx, "text": f"line {lid}", "score": score, "url": f"https://acc.md/{cid}.pdf",
            "found_on": "https://acc.md/acte", "citation_label": f"Act {cid}", "page": 2,
            "deep_link": f"https://acc.md/{cid}.pdf#page=2"} | fields


def ranks(vec=None, line=None, fts=None) -> dict:
    return {"vec_rank": vec, "line_rank": line, "fts_rank": fts}


def test_rankings_fuse_and_every_chunk_gets_its_row_and_lines(index, db):
    every_way(index)

    result = retrieve(db, QUERY, **WEIGHTS)

    c1, c2, c3, c4 = (1 / 61 + 1 / 62), (1 / 62 + 0.1 / 61), 1 / 61, 0.1 / 62
    assert result.items == [
        # at most three lines, the best first, whichever query found them
        chunk("c1") | ranks(vec=1, line=2) | {"score": pytest.approx(c1), "rrf_score": pytest.approx(c1),
                                              "matched_lines": [matched("l1a", "c1", 0, 0.7),
                                                                matched("l1c", "c1", 3, 0.65),
                                                                matched("l1b", "c1", 4, 0.6)]},
        chunk("c2", **PAGE) | ranks(vec=2, fts=1) | {
            "score": pytest.approx(c2), "rrf_score": pytest.approx(c2),
            # a page has no pages: its lines get no page, and the link points at their text
            "matched_lines": [{"line_id": "l2", "idx": 1, "text": "line l2", "score": 0.3,
                               "url": "https://acc.md/ro/servicii", "found_on": None, "citation_label": "Act c2",
                               "deep_link": "https://acc.md/ro/servicii#:~:text=line%20l2"}]},
        # found by its lines only: the chunk row comes from get_chunks_by_ids
        chunk("c3") | ranks(line=1) | {"score": pytest.approx(c3), "rrf_score": pytest.approx(c3),
                                       "matched_lines": [matched("l3", "c3", 0, 0.85)]},
        # no line matched: its first lines stand in, with their own page and boxes and score 0
        chunk("c4") | ranks(fts=2) | {
            "score": pytest.approx(c4), "rrf_score": pytest.approx(c4),
            "matched_lines": [matched("l4a", "c4", 0, 0.0, page=5, bboxes=[{"page": 5}],
                                      deep_link="https://acc.md/c4.pdf#page=5"),
                              matched("l4b", "c4", 1, 0.0, bboxes=[])]},
    ]
    assert result.not_found is False
    # c5 was found by line FTS only, which adds lines but doesn't rank: it is neither fetched nor returned
    assert index.chunk_requests == [["c3"]]
    assert index.first_line_requests == [["c4"]]


def test_the_query_and_the_filters_reach_every_query(index, db):
    every_way(index)
    filters = {"lang": "ro", "site": "acc.md", "sites": ["acc.md", "dgaurf.md"], "date_after": "2020-01-01"}

    result = retrieve(db, QUERY, k=1, top_candidates=5, **filters, **WEIGHTS)

    assert [c["chunk_id"] for c in result.items] == ["c1"]
    assert index.model.calls == [([QUERY], True)]
    lines_limit = filters | {"limit": 10}  # line queries take twice as many rows: several lines share a chunk
    expected = {"chunk_vector": filters | {"limit": 5}, "line_vector": lines_limit,
                "chunk_fts": filters | {"limit": 5}, "line_fts": lines_limit}
    assert {name: [kw for _, kw in calls] for name, calls in index.calls.items()} == {
        name: [kw] for name, kw in expected.items()}
    for name in ("chunk_vector", "line_vector"):
        [(q_vec, _)] = index.calls[name]
        assert q_vec.dtype == np.float32 and q_vec.tolist() == pytest.approx([0.6, 0.8])
    assert index.calls["chunk_fts"][0][0] == index.calls["line_fts"][0][0] == build_fts_query(QUERY)


def test_weights_default_to_the_config(index, db, monkeypatch):
    index.results["chunk_vector"] = [hit("c1", 0.9)]
    index.results["chunk_fts"] = [hit("c2", 0.5)]
    monkeypatch.setattr(pipeline, "W_VECTOR", 0.5)
    monkeypatch.setattr(pipeline, "W_FTS", 2.0)

    result = retrieve(db, QUERY)

    assert [(c["chunk_id"], c["rrf_score"]) for c in result.items] == [
        ("c2", pytest.approx(2.0 / 61)), ("c1", pytest.approx(0.5 / 61))]


def test_a_page_and_an_act_with_the_same_text_are_one_hit(index, db):
    index.results["chunk_vector"] = [hit("c1", 0.9, **PAGE, content_hash="same"), hit("c2", 0.8, content_hash="same")]

    result = retrieve(db, QUERY, **WEIGHTS)

    # the act's row, with the better score of the two
    assert [(c["chunk_id"], c["rrf_score"]) for c in result.items] == [("c2", pytest.approx(1 / 61))]


def test_nothing_found(index, db):
    result = retrieve(db, QUERY, **WEIGHTS)

    assert result.items == [] and result.not_found is True
    assert index.chunk_requests == [] and index.first_line_requests == []


def test_timings_in_pool_mode_take_the_slower_query_of_each_kind(index):
    every_way(index)
    index.barrier = threading.Barrier(4)  # each query waits for the other three: they run at once, or this times out

    result = retrieve(Pool(index.conn), QUERY, **WEIGHTS)

    assert set(result.timings_ms) == {"embed", "vector_sql", "fts_sql", "rerank", "total"}
    # vector_sql = max(chunk vector 10, line vector 30), fts_sql = max(chunk FTS 2, line FTS 5)
    assert {k: v for k, v in result.timings_ms.items() if k != "total"} == {
        "embed": 4.0, "vector_sql": 30.0, "fts_sql": 5.0, "rerank": 0.0}


def test_timings_on_one_connection_add_the_queries_up(index):
    every_way(index)

    result = retrieve(index.conn, QUERY, **WEIGHTS)

    assert result.timings_ms == {"embed": 4.0, "vector_sql": 40.0, "fts_sql": 7.0, "rerank": 0.0, "total": 51.0}


@pytest.mark.parametrize("top_score,not_found", [(NOT_FOUND_THRESHOLD, False), (NOT_FOUND_THRESHOLD / 2, True)])
def test_rerank_orders_the_candidates_and_its_top_score_decides_not_found(index, db, monkeypatch, top_score, not_found):
    index.results["chunk_vector"] = [hit("c1", 0.9), hit("c2", 0.8), hit("c3", 0.7)]
    calls = []

    def rerank_candidates(query, candidates, top_k, device):
        calls.append((query, [c["chunk_id"] for c in candidates], top_k, device))
        index.clock.advance("rerank")
        scores = {"c1": top_score / 4, "c2": top_score / 2, "c3": top_score}
        reranked = sorted((c | {"rerank_score": scores[c["chunk_id"]]} for c in candidates),
                          key=lambda c: c["rerank_score"], reverse=True)
        return reranked[:top_k]

    monkeypatch.setattr(pipeline, "RERANKER_ENABLED", True)
    monkeypatch.setattr(pipeline, "rerank_candidates", rerank_candidates)

    result = retrieve(db, QUERY, k=2, rerank=True, **WEIGHTS)

    # the reranker sees every candidate, not only the first k
    assert calls == [(QUERY, ["c1", "c2", "c3"], 2, "test-device")]
    assert [c["chunk_id"] for c in result.items] == ["c3", "c2"]
    assert result.not_found is not_found
    assert result.timings_ms["rerank"] == 20.0


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="weighted_rrf_fuse keeps the row of the first ranking that has the chunk, here the line "
                          "ranking's bare {chunk_id, score}, and retrieve() fetches rows only for chunks no chunk "
                          "query found. Fixed by merging the rows in weighted_rrf_fuse.")
def test_a_chunk_found_by_line_vector_and_chunk_fts_keeps_its_row(index, db):
    index.results["line_vector"] = [line("l1", "c1", 0, 0.8)]
    index.results["chunk_fts"] = [hit("c1", 0.4)]

    (item,) = retrieve(db, QUERY, **WEIGHTS).items

    assert {k: item.get(k) for k in ("doc_id", "text", "url")} == {
        "doc_id": "d_c1", "text": "text of c1", "url": "https://acc.md/c1.pdf"}
    # /api/tools/search raised KeyError: 'doc_id' on it
    assert search_tool(db, QUERY)["items"][0]["doc_id"] == "d_c1"
