"""Questions the bot couldn't answer (`not_found`) or answered only partly (`partial`): the admin's "gaps"
(GET /api/admin/gaps, docs/tasks/11 D). Which documents the city hall is missing.

Similar questions are one group: cosine ≥ 0.85 of their local bge-m3 embeddings, no LLM. A group's id is the
answer_id of its first (oldest) question, so actions (hide, re-check) work on a list computed on the fly.
Questions are masked like the public wall (e-mails, phones, long digit runs).
"""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import numpy as np
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .schemas import AskRequest, AskResponse
from .wall import mask

SAME_GAP = 0.85
WORSE = {"not_found": 2, "partial": 1}
MAX_QUESTIONS_SHOWN = 20


def iso(value) -> str:
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else str(value)


def group(rows: list[dict], embed: Callable[[list[str]], np.ndarray], threshold: float = SAME_GAP) -> list[list[dict]]:
    """Rows oldest first → groups of similar questions; a question joins the first group whose first question
    it is close to."""
    rows = sorted(rows, key=lambda r: r["created_at"])
    if not rows:
        return []
    vectors = embed([r["question"] for r in rows])
    heads: list[int] = []
    groups: list[list[dict]] = []
    for i, row in enumerate(rows):
        near = next((g for g, h in enumerate(heads) if float(vectors[i] @ vectors[h]) >= threshold), None)
        if near is None:
            heads.append(i)
            groups.append([row])
        else:
            groups[near].append(row)
    return groups


def gap_item(members: list[dict]) -> dict:
    head = members[0]
    sites = Counter(site for m in members for site in (m.get("retrieved_sites") or []))
    missing = list(dict.fromkeys(x for m in members for x in (m.get("missing") or [])))
    return {
        "id": head["answer_id"],
        "example": mask(head["question"]),
        "questions": [{"answer_id": m["answer_id"], "question": mask(m["question"]), "lang": m["lang"],
                       "status": m["status"], "ts": iso(m["created_at"])} for m in members[-MAX_QUESTIONS_SHOWN:]],
        "count": len(members),
        "last_asked": iso(max(m["created_at"] for m in members)),
        "langs": sorted({m["lang"] for m in members}),
        "status": max((m["status"] for m in members), key=lambda st: WORSE.get(st, 0)),
        "missing": missing[:10],
        "hint_sites": [{"site": site, "hits": hits} for site, hits in sites.most_common(3)],
        "rechecked": head.get("recheck"),
        "hidden": any(m.get("gap_hidden") for m in members),
    }


def gaps(rows: list[dict], embed: Callable[[list[str]], np.ndarray], *, hidden: bool = False,
         limit: int = 50) -> dict:
    """The admin's list: groups sorted by size then recency; hidden groups only with hidden=True; a group whose
    re-check came back answered is solved and left out."""
    items = [gap_item(members) for members in group(rows, embed)]
    items = [i for i in items if (hidden or not i["hidden"]) and (i["rechecked"] or {}).get("status") != "answered"]
    ordered = sorted(items, key=lambda i: (i["count"], i["last_asked"]), reverse=True)
    return {"items": ordered[:limit],
            "totals": {"not_found": sum(r["status"] == "not_found" for r in rows),
                       "partial": sum(r["status"] == "partial" for r in rows), "groups": len(ordered)}}


class PgGaps:
    def __init__(self, pool: ConnectionPool, embed: Callable[[list[str]], np.ndarray] | None = None):
        from .suggestions import default_embed

        self.pool = pool
        self.embed = embed or default_embed

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def rows(self, statuses: list[str], lang: str | None, days: int) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        return self._rows(
            "SELECT answer_id, question, lang, status, created_at, missing, retrieved_sites, gap_hidden, recheck "
            "FROM answers WHERE status = ANY(%s) AND created_at >= %s AND (%s::text IS NULL OR lang = %s) "
            "ORDER BY created_at", (statuses, since, lang, lang))

    def list(self, statuses: list[str], lang: str | None, days: int, limit: int, hidden: bool) -> dict:
        return gaps(self.rows(statuses, lang, days), self.embed, hidden=hidden, limit=limit)

    def members(self, gap_id: str) -> list[dict]:
        """The group a gap id stands for, grouped the same way over every unanswered question."""
        rows = self.rows(["not_found", "partial"], None, 3650)
        return next((g for g in group(rows, self.embed) if g[0]["answer_id"] == gap_id), [])

    def hide(self, gap_id: str, hidden: bool) -> bool:
        members = self.members(gap_id)
        if members:
            self._rows("UPDATE answers SET gap_hidden = %s WHERE answer_id = ANY(%s)",
                       (hidden, [m["answer_id"] for m in members]))
        return bool(members)

    def recheck(self, gap_id: str, answer: Callable[[AskRequest], AskResponse]) -> dict | None:
        """The group's example asked again through the normal answer pipeline: one question, one model call."""
        members = self.members(gap_id)
        if not members:
            return None
        head = members[0]
        r = answer(AskRequest(question=head["question"], lang=head["lang"]))
        result = {"status": r.status, "verified": r.meta.verified, "answer_id": r.id,
                  "ts": datetime.now(UTC).isoformat(timespec="seconds")}
        self._rows("UPDATE answers SET recheck = %s WHERE answer_id = %s", (Jsonb(result), gap_id))
        return result
