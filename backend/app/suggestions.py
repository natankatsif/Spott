"""Quick questions (GET /api/suggestions, docs/tasks/09 §3): real questions people ask often that we answer well.

A question qualifies when it was answered (`answered`, verified, with a citation), no one rated it 2 stars or less
and its average is 4+ if rated, it was asked at least twice (or the admin pinned it), nothing in it looks personal
(wall.mask leaves it as it is), it is 10-120 characters long. Near-identical wordings (cosine > 0.9, bge-m3) are one
question, in its most frequent wording. Every candidate is asked again after the index changes and at least once a
day (`recheck`); one that is no longer answered and verified is dropped. The last good answer is kept per index
version and replayed when the quick question is clicked (meta.path = "cache").
With no candidates yet, the seed list (data/suggestions_seed.json) goes through the same check.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .schemas import AskRequest, AskResponse, Suggestion
from .wall import mask

log = logging.getLogger("backend.suggestions")

SEED = Path(__file__).resolve().parent / "data" / "suggestions_seed.json"
MIN_ASKED = 2
MIN_CHARS, MAX_CHARS = 10, 120
SAME_QUESTION = 0.9  # cosine similarity above which two wordings are one question
RECHECK_EVERY = timedelta(days=1)
MAX_RECHECKS = 20  # answers per re-check round (each is an LLM call)


def normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().rstrip("?!. ").casefold()


def qualifies(question: str) -> bool:
    return MIN_CHARS <= len(question.strip()) <= MAX_CHARS and mask(question) == question


def good_answer(r: AskResponse) -> bool:
    return r.status == "answered" and r.meta.verified and bool(r.citations)


def merge_similar(rows: list[dict], embed: Callable[[list[str]], np.ndarray]) -> list[dict]:
    """One row per question: wordings with cosine similarity > SAME_QUESTION join the most frequent one."""
    rows = sorted(rows, key=lambda r: -r["asked_count"])
    if len(rows) < 2:
        return rows
    vectors = embed([r["question"] for r in rows])
    kept: list[int] = []
    for i, row in enumerate(rows):
        twin = next((k for k in kept if float(vectors[i] @ vectors[k]) > SAME_QUESTION), None)
        if twin is None:
            kept.append(i)
        else:
            rows[twin]["asked_count"] += row["asked_count"]
    return [rows[k] for k in kept]


def choose(rows: list[dict], embed: Callable[[list[str]], np.ndarray]) -> list[dict]:
    """The rules of a quick question, over the answers log grouped by question."""
    good = [r for r in rows if r["always_good"] and qualifies(r["question"])
            and (r["rating_min"] is None or r["rating_min"] > 2)
            and (r["rating_avg"] is None or r["rating_avg"] >= 4) and r["asked_count"] >= MIN_ASKED]
    return [r for lang in ("ro", "ru") for r in merge_similar([g for g in good if g["lang"] == lang], embed)]


class PgSuggestions:
    def __init__(self, pool: ConnectionPool, embed: Callable[[list[str]], np.ndarray] | None = None):
        self.pool = pool
        self.embed = embed or default_embed

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def index_version(self) -> str:
        [row] = self._rows("SELECT COALESCE(MAX(indexed_at)::text, '') || '/' || COUNT(*) AS v FROM documents")
        return row["v"]

    def candidates(self) -> list[dict]:
        """Questions from the answers log that pass the rules, near-identical wordings merged."""
        rows = self._rows(
            """
            SELECT a.lang, MIN(a.question) AS question, COUNT(*) AS asked_count,
                   (ARRAY_AGG(a.answer_id ORDER BY a.created_at DESC))[1] AS answer_id,
                   BOOL_AND(a.status = 'answered' AND a.verified AND a.citations > 0) AS always_good,
                   AVG(f.rating)::float AS rating_avg, MIN(f.rating) AS rating_min
            FROM answers a LEFT JOIN feedback f ON f.answer_id = a.answer_id
            GROUP BY a.lang, lower(regexp_replace(trim(a.question), '[?!. ]+$', ''))
            """)
        return choose(rows, self.embed)

    def refresh(self) -> int:
        """Candidates into the table (counts and ratings updated), seeds while there are none; returns how many."""
        found = self.candidates()
        if not found and not self._rows("SELECT 1 FROM suggestions LIMIT 1"):
            seed = json.loads(SEED.read_text(encoding="utf-8"))
            found = [{"lang": lang, "question": q, "asked_count": 0, "rating_avg": None, "answer_id": None}
                     for lang, questions in seed.items() for q in questions]
        for r in found:
            self._rows(
                "INSERT INTO suggestions (question, lang, asked_count, rating_avg, answer_id) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (lang, question) DO UPDATE SET asked_count = EXCLUDED.asked_count, "
                "rating_avg = EXCLUDED.rating_avg, answer_id = COALESCE(EXCLUDED.answer_id, suggestions.answer_id)",
                (r["question"], r["lang"], r["asked_count"], r["rating_avg"], r["answer_id"]))
        return len(found)

    def due(self, version: str, now: datetime | None = None) -> list[dict]:
        """Suggestions not checked against this index version, or not in the last day."""
        now = now or datetime.now(UTC)
        return self._rows(
            "SELECT id, question, lang FROM suggestions WHERE NOT hidden AND (index_version IS DISTINCT FROM %s "
            "OR checked_at IS NULL OR checked_at < %s) ORDER BY pinned DESC, asked_count DESC LIMIT %s",
            (version, now - RECHECK_EVERY, MAX_RECHECKS))

    def recheck(self, answer: Callable[[AskRequest], AskResponse]) -> dict[str, int]:
        """Asks every due suggestion again; keeps the answer if it is still answered and verified."""
        version, checked, dropped = self.index_version(), 0, 0
        for s in self.due(version):
            try:
                r = answer(AskRequest(question=s["question"], lang=s["lang"]))
            except Exception as e:  # no model (no credits, no network): try again next round
                log.warning("suggestion %s not re-checked: %s", s["id"], e)
                break
            ok = good_answer(r)
            checked, dropped = checked + 1, dropped + (not ok)
            self._rows("UPDATE suggestions SET ok = %s, checked_at = NOW(), index_version = %s, cached = %s, "
                       "answer_id = %s WHERE id = %s",
                       (ok, version, Jsonb(r.model_dump()) if ok else None, r.id, s["id"]))
        return {"checked": checked, "dropped": dropped}

    def list(self, lang: str, limit: int) -> list[Suggestion]:
        rows = self._rows("SELECT * FROM suggestions WHERE ok AND NOT hidden AND lang = %s "
                          "ORDER BY pinned DESC, asked_count DESC, id LIMIT %s", (lang, limit))
        return [Suggestion(id=r["id"], question=r["question"], lang=r["lang"], answer_id=r["answer_id"],
                           asked_count=r["asked_count"], rating_avg=r["rating_avg"], pinned=r["pinned"]) for r in rows]

    def add(self, question: str, lang: str, pinned: bool) -> Suggestion:
        [r] = self._rows("INSERT INTO suggestions (question, lang, pinned) VALUES (%s, %s, %s) "
                         "ON CONFLICT (lang, question) DO UPDATE SET pinned = EXCLUDED.pinned, hidden = FALSE "
                         "RETURNING *", (question.strip(), lang, pinned))
        return Suggestion(id=r["id"], question=r["question"], lang=r["lang"], answer_id=r["answer_id"],
                          asked_count=r["asked_count"], rating_avg=r["rating_avg"], pinned=r["pinned"])

    def hide(self, suggestion_id: int) -> bool:
        return bool(self._rows("UPDATE suggestions SET hidden = TRUE WHERE id = %s RETURNING id", (suggestion_id,)))

    def cached(self, req: AskRequest) -> AskResponse | None:
        """The checked answer of a quick question, if it was checked against the current index."""
        if req.history:
            return None
        rows = self._rows("SELECT cached, index_version FROM suggestions WHERE ok AND NOT hidden AND cached IS NOT NULL "
                          "AND lower(question) = lower(%s) AND (%s::text IS NULL OR lang = %s)",
                          (req.question.strip(), req.lang, req.lang))
        if not rows or rows[0]["index_version"] != self.index_version():
            return None
        return AskResponse.model_validate(rows[0]["cached"])


def default_embed(texts: list[str]) -> np.ndarray:
    from retrieval.embeddings import get_device, get_embedding_model

    return np.asarray(get_embedding_model(get_device()).encode(texts, normalize_embeddings=True))
