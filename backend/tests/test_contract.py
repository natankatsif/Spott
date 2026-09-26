"""Every frontend mock (frontend/src/lib/mocks/**/*.json) must validate against the backend models,
so a contract change on either side fails here instead of in the demo (docs/API.md)."""

import json
from pathlib import Path

import pytest

from app.schemas import AskResponse, CorpusStats, WallResponse

MOCKS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "mocks"
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


def test_wall_mock_matches_contract():
    WallResponse.model_validate(load(MOCKS / "wall.json"))


def test_corpus_stats_mock_matches_contract():
    CorpusStats.model_validate(load(MOCKS / "corpus-stats.json"))
