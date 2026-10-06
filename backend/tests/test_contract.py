"""Every frontend mock (frontend/src/lib/mocks/**/*.json) must validate against the backend models,
so a contract change on either side fails here instead of in the demo (docs/API.md)."""

import json
from pathlib import Path

import pytest

from app.schemas import (
    AdminSession,
    ApiError,
    AskResponse,
    CorpusStats,
    FeedbackList,
    FeedbackStats,
    GapList,
    GapRecheck,
    Job,
    JobList,
    SourceAdded,
    SourceList,
    SuggestionList,
    WallResponse,
)

MOCKS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "mocks"
PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"
ASK_MOCKS = sorted((MOCKS / "ask").glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_mocks_exist():
    assert ASK_MOCKS, f"no mocks in {MOCKS / 'ask'}"


@pytest.mark.parametrize("path", ASK_MOCKS, ids=lambda p: p.stem)
def test_ask_mock_matches_contract(path):
    r = AskResponse.model_validate(load(path))

    ids = [c.id for c in r.citations]
    assert len(ids) == len(set(ids))
    cited = {cid for s in r.sentences for cid in s.cites}
    if r.checklist:
        cited |= {cid for step in r.checklist.steps for cid in step.cites}
    assert cited <= set(ids), "a sentence cites a citation that isn't in the list"
    assert set(ids) <= cited, "every citation must be referenced by a sentence or checklist step"
    assert r.answer == " ".join(s.text for s in r.sentences)
    for c in r.citations:
        assert c.deep_link.startswith(c.url)
        if c.quote_lang != r.lang:
            assert c.translation, f"{c.id}: quote in {c.quote_lang}, answer in {r.lang}, no translation"
    if r.status == "conflict":
        assert r.conflict and set(r.conflict.citation_ids) <= set(ids)
    if r.status in ("not_found", "refused"):
        assert r.citations == []
    for c in r.citations:  # the source preview: a real static preview in mock mode (backend/scripts/export_previews.py)
        assert c.preview_url.startswith("/mocks/preview/"), c.preview_url
        page = PUBLIC / c.preview_url.lstrip("/")
        assert page.is_file(), f"{c.id}: {page} missing, run backend/scripts/export_previews.py"
        assert c.preview_kind == ("pdf" if c.file_url else "page" if c.kind == "page" else "text")
    for c in r.contacts:  # who can help: only without a full answer, and a link to where the contact is written
        assert r.status in ("not_found", "partial")
        assert c.deep_link.startswith(c.url) and c.line_ids


def test_wall_mock_matches_contract():
    WallResponse.model_validate(load(MOCKS / "wall.json"))


def test_corpus_stats_mock_matches_contract():
    CorpusStats.model_validate(load(MOCKS / "corpus-stats.json"))


@pytest.mark.parametrize("name,model", [
    ("suggestions.json", SuggestionList), ("admin/session.json", AdminSession), ("admin/sources.json", SourceList), ("admin/job.json", Job),
    ("admin/jobs.json", JobList), ("admin/feedback.json", FeedbackList), ("admin/feedback-stats.json", FeedbackStats),
])
def test_task09_mocks_match_contract(name, model):
    model.model_validate(load(MOCKS / name))


@pytest.mark.parametrize("name,model", [
    ("admin/add-source-site.json", SourceAdded), ("admin/add-source-document.json", SourceAdded),
    ("admin/add-source-merged.json", SourceAdded), ("admin/add-source-blocked.json", SourceAdded),
    ("admin/add-source-unreachable.json", ApiError), ("admin/gaps.json", GapList),
    ("admin/gap-recheck.json", GapRecheck),
])
def test_task11_admin_mocks_match_contract(name, model):
    model.model_validate(load(MOCKS / name))


def test_task11_sources_mock_shows_every_state():
    """The demo admin page: all 40 sites of sites.toml, a running job with progress, a failure, a document."""
    rows = SourceList.model_validate(load(MOCKS / "admin" / "sources.json")).sources
    assert len({r.site_id for r in rows if r.kind == "site"}) == 40
    states = {r.status for r in rows}
    assert {"indexed", "pending", "running", "failed", "blocked"} <= states
    running = next(r for r in rows if r.status == "running")
    assert running.progress and 0 < running.progress.percent < 100
    assert next(r for r in rows if r.status == "failed").last_error
    assert any(r.kind == "document" for r in rows)
    merged = SourceAdded.model_validate(load(MOCKS / "admin" / "add-source-merged.json"))
    assert merged.merged_into == merged.id


def test_task11_gaps_mock_is_consistent():
    """Shaped like app/gaps.py builds it: the example is the first question, count = questions, the worst status,
    a group re-checked as answered is not listed."""
    gaps = GapList.model_validate(load(MOCKS / "admin" / "gaps.json"))
    for g in gaps.items:
        assert g.example == g.questions[0].question and g.id == g.questions[0].answer_id
        assert g.count == len(g.questions)
        assert g.status == ("not_found" if any(q.status == "not_found" for q in g.questions) else "partial")
        assert not g.rechecked or g.rechecked.status != "answered"
    questions = [q for g in gaps.items for q in g.questions]
    assert gaps.totals.groups == len(gaps.items)
    assert gaps.totals.not_found == sum(q.status == "not_found" for q in questions)
    assert gaps.totals.partial == sum(q.status == "partial" for q in questions)
