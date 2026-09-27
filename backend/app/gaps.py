"""Questions the bot couldn't answer (`not_found`) or answered only partly (`partial`): the admin's "gaps"
(GET /api/admin/gaps, docs/tasks/11 D). Which documents the city hall is missing.

Similar questions are one group: cosine ≥ 0.85 of their local bge-m3 embeddings, no LLM. A group's id is the
answer_id of its first (oldest) question, so actions (hide, re-check) work on a list computed on the fly.
Questions are masked like the public wall (e-mails, phones, long digit runs). Each group gets a topic (transport,
education, …) from a small model, once: it is stored on the group's first answer.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import numpy as np
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .schemas import AskRequest, AskResponse
from .wall import mask

log = logging.getLogger("backend.gaps")

SAME_GAP = 0.85
TOPICS = ("transport", "urbanism", "education", "health", "social", "utilities", "taxes", "documents", "council",
          "environment", "culture", "other")
TOPIC_PROMPT = """\
Sort questions residents asked the Chișinău City Hall assistant into topics. Topics: transport (public transport, \
parking, roads, traffic); urbanism (construction, permits, urban plans, land); education (kindergartens, schools); \
health (hospitals, clinics, doctors); social (benefits, pensions, disability, elderly); utilities (housing, water, \
heating, waste, lighting); taxes (local taxes, fees, payments, fines); documents (certificates, applications, civil \
status, appointments, petitions); council (municipal council, decisions, the mayor, the city budget); environment \
(green areas, parks, trees, animals); culture (culture, sport, events, youth, tourism); other. Return one topic per \
question, in the same order."""
TOPIC_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["topics"],
                "properties": {"topics": {"type": "array", "items": {"type": "string", "enum": list(TOPICS)}}}}
MAX_TOPIC_BATCH = 60
# questions → their topics in the same order (None where unknown)
Classify = Callable[[list[str]], list[str | None]]
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
        "topic": head.get("topic") if head.get("topic") in TOPICS else "other",
        "last_answer": next((m.get("answer") for m in reversed(members) if m["status"] == "partial" and m.get("answer")),
                            None),
    }


def gaps(rows: list[dict], embed: Callable[[list[str]], np.ndarray], *, hidden: bool = False,
         limit: int = 50, classify: Callable[[list[dict]], dict[str, str]] | None = None) -> dict:
    """The admin's list: groups sorted by size then recency; hidden groups only with hidden=True; a group whose
    re-check came back answered is solved and left out. `classify` gives topics to groups that have none yet."""
    groups = group(rows, embed)
    if classify and (untopiced := [g[0] for g in groups if g[0].get("topic") not in TOPICS]):
        found = classify(untopiced)
        for g in groups:
            g[0]["topic"] = g[0].get("topic") or found.get(g[0]["answer_id"])
    items = [gap_item(members) for members in groups]
    items = [i for i in items if (hidden or not i["hidden"]) and (i["rechecked"] or {}).get("status") != "answered"]
    ordered = sorted(items, key=lambda i: (i["count"], i["last_asked"]), reverse=True)
    topics = Counter(i["topic"] for i in ordered)
    return {"items": ordered[:limit],
            "totals": {"not_found": sum(r["status"] == "not_found" for r in rows),
                       "partial": sum(r["status"] == "partial" for r in rows), "groups": len(ordered)},
            "topics": [{"topic": t, "groups": n} for t, n in topics.most_common()]}


def llm_classify(llm) -> Classify:
    """Topics of questions, by the small model, in batches; [None, …] when it can't be reached."""
    from .llm import REWRITE_MODEL

    def classify(questions: list[str]) -> list[str | None]:
        out: list[str | None] = []
        for start in range(0, len(questions), MAX_TOPIC_BATCH):
            batch = questions[start:start + MAX_TOPIC_BATCH]
            user = "\n".join(f"{i + 1}. {q[:200]}" for i, q in enumerate(batch))
            try:
                r = llm().complete_json(TOPIC_PROMPT, user, "topics", TOPIC_SCHEMA, model=REWRITE_MODEL,
                                        effort="none", max_tokens=20 + 8 * len(batch))
                got = [t if t in TOPICS else None for t in r.data.get("topics") or []]
            except Exception as e:  # noqa: BLE001 - no topics this time: asked again on the next list
                log.warning("gap topics not classified: %s", e)
                got = []
            out += (got + [None] * len(batch))[:len(batch)]
        return out

    return classify


class PgGaps:
    def __init__(self, pool: ConnectionPool, embed: Callable[[list[str]], np.ndarray] | None = None,
                 classify: Classify | None = None):
        from .suggestions import default_embed

        self.pool = pool
        self.embed = embed or default_embed
        self.classify = classify

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def rows(self, statuses: list[str], lang: str | None, days: int) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        return self._rows(
            "SELECT answer_id, question, lang, status, created_at, missing, retrieved_sites, gap_hidden, recheck, topic, "
            "answer "
            "FROM answers WHERE status = ANY(%s) AND created_at >= %s AND (%s::text IS NULL OR lang = %s) "
            "ORDER BY created_at", (statuses, since, lang, lang))

    def list(self, statuses: list[str], lang: str | None, days: int, limit: int, hidden: bool) -> dict:
        return gaps(self.rows(statuses, lang, days), self.embed, hidden=hidden, limit=limit,
                    classify=self.store_topics if self.classify else None)

    def store_topics(self, heads: list[dict]) -> dict[str, str]:
        """Topics for groups without one, kept on the group's first answer so each is classified once."""
        assert self.classify is not None
        found = {h["answer_id"]: t for h, t in zip(heads, self.classify([h["question"] for h in heads]), strict=True)
                 if t}
        for answer_id, topic in found.items():
            self._rows("UPDATE answers SET topic = %s WHERE answer_id = %s", (topic, answer_id))
        return found

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
