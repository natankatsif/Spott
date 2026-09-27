"""Questions the bot couldn't answer (`not_found`) or answered only partly (`partial`): the admin's "gaps"
(GET /api/admin/gaps, docs/tasks/11 D). Which documents the city hall is missing.

Grouping in two steps. Near-identical wordings (cosine ≥ 0.85 of their local bge-m3 embeddings) are one
"wording group", no LLM. Then a small model puts each new wording group into a group of questions that one missing
document would answer (the same service, fee, procedure, place or schedule, in any language and wording), or starts
a new one with a short title, and gives it a topic. That decision is stored on the wording group's first answer
(answers.gap_group, answers.topic; the title in answers.gap_title of the group's first answer), so each question is
sorted once and groups stay put. A group's id is the answer_id of its first question.
Questions are masked like the public wall (e-mails, phones, long digit runs).
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
CLUSTER_PROMPT = """\
You sort questions that residents asked the Chișinău City Hall assistant and that it could not (fully) answer, so \
an admin sees which information is missing from its documents.
Two questions are in one group when they are about the same service, procedure or document, so one missing \
document would answer both, even worded differently, in another language, or asking a different detail of it (its \
price, deadline, where or how to get it: "how much is a garage permit" and "how long does a building permit take" \
go together). Different services never go together. Prefer fewer, broader groups an admin can act on.
For every new question give its topic and its group: the id of an existing group ("G3") it belongs to, or a new \
label ("N1", "N2", …) shared by the new questions that belong together. For each new label give a title of at most \
60 characters naming the service or document that is missing, in Romanian and in Russian ("Autorizația de \
construire" / "Разрешение на строительство").
Topics: transport (public transport, parking, roads, traffic); urbanism (construction, permits, urban plans, land); \
education (kindergartens, schools); health (hospitals, clinics, doctors); social (benefits, pensions, disability, \
elderly); utilities (housing, water, heating, waste, lighting); taxes (local taxes, fees, payments, fines); \
documents (certificates, applications, civil status, appointments, petitions); council (municipal council, \
decisions, the mayor, the city budget); environment (green areas, parks, trees, animals); culture (culture, sport, \
events, youth, tourism); other."""
CLUSTER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items", "new_groups"],
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "topic", "group"],
            "properties": {"n": {"type": "integer"}, "topic": {"type": "string", "enum": list(TOPICS)},
                           "group": {"type": "string"}}}},
        "new_groups": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["group", "ro", "ru"],
            "properties": {"group": {"type": "string"}, "ro": {"type": "string"}, "ru": {"type": "string"}}}},
    },
}
MAX_CLUSTER_BATCH = 40
MAX_EXISTING_SHOWN = 300
MAX_TITLE = 80
# (new wording groups' first rows, existing groups {id, title, topic}) → {"group": {row id: group id},
# "topic": {row id: topic}, "title": {group id: {"ro", "ru"}}}; ids it leaves out stay unsorted until the next list
Cluster = Callable[[list[dict], list[dict]], dict]
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


def gap_item(members: list[dict], key: str | None = None, title: dict | None = None) -> dict:
    """One group; `key` is its id (the first question of the group), `members` oldest first."""
    head = next((m for m in members if m["answer_id"] == key), members[0])
    sites = Counter(site for m in members for site in (m.get("retrieved_sites") or []))
    missing = list(dict.fromkeys(x for m in members for x in (m.get("missing") or [])))
    return {
        "id": head["answer_id"],
        "example": mask(head["question"]),
        "title": title if title and title.get("ro") and title.get("ru") else None,
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


def merged(rows: list[dict], embed: Callable[[list[str]], np.ndarray],
           cluster: Cluster | None = None) -> list[tuple[str, list[dict], dict | None]]:
    """(group id, members oldest first, title) per group: the wording groups joined as the model sorted them
    (stored, or asked now for the ones never sorted)."""
    wording = group(rows, embed)
    heads = [w[0] for w in wording]
    new = [h for h in heads if not h.get("gap_group")]
    if cluster and new:
        by_id = {r["answer_id"]: r for r in rows}
        existing = []
        for key in dict.fromkeys(h["gap_group"] for h in heads if h.get("gap_group")):
            first = by_id.get(key, {})
            title = first.get("gap_title") or {}
            existing.append({"id": key, "topic": first.get("topic") or "other",
                             "title": title.get("ro") or title.get("ru") or mask(first.get("question") or key)})
        found = cluster(new, existing[-MAX_EXISTING_SHOWN:])
        for h in new:
            if h["answer_id"] in found["group"]:
                h["gap_group"] = found["group"][h["answer_id"]]
                h["topic"] = found["topic"].get(h["answer_id"]) or h.get("topic")
        for r in rows:
            if r["answer_id"] in found["title"]:
                r["gap_title"] = found["title"][r["answer_id"]]
    by_key: dict[str, list[dict]] = {}
    for w in wording:
        by_key.setdefault(w[0].get("gap_group") or w[0]["answer_id"], []).extend(w)
    titles = {r["answer_id"]: r.get("gap_title") for r in rows}
    return [(key, sorted(members, key=lambda m: m["created_at"]), titles.get(key)) for key, members in by_key.items()]


def gaps(rows: list[dict], embed: Callable[[list[str]], np.ndarray], *, hidden: bool = False,
         limit: int = 50, cluster: Cluster | None = None) -> dict:
    """The admin's list: groups sorted by size then recency; hidden groups only with hidden=True; a group whose
    re-check came back answered is solved and left out."""
    items = [gap_item(members, key, title) for key, members, title in merged(rows, embed, cluster)]
    items = [i for i in items if (hidden or not i["hidden"]) and (i["rechecked"] or {}).get("status") != "answered"]
    ordered = sorted(items, key=lambda i: (i["count"], i["last_asked"]), reverse=True)
    topics = Counter(i["topic"] for i in ordered)
    return {"items": ordered[:limit],
            "totals": {"not_found": sum(r["status"] == "not_found" for r in rows),
                       "partial": sum(r["status"] == "partial" for r in rows), "groups": len(ordered)},
            "topics": [{"topic": t, "groups": n} for t, n in topics.most_common()]}


def llm_cluster(llm) -> Cluster:
    """The small model sorts new wording groups into groups, in batches; what it can't do stays unsorted."""
    from .llm import REWRITE_MODEL

    def cluster(new: list[dict], existing: list[dict]) -> dict:
        out: dict = {"group": {}, "topic": {}, "title": {}}
        existing = list(existing)
        for start in range(0, len(new), MAX_CLUSTER_BATCH):
            batch = new[start:start + MAX_CLUSTER_BATCH]
            ids = {f"G{i + 1}": e["id"] for i, e in enumerate(existing)}
            user = ("Existing groups:\n" + "".join(f"G{i + 1} [{e['topic']}] {e['title'][:MAX_TITLE]}\n"
                                                    for i, e in enumerate(existing))
                    if existing else "Existing groups: none\n")
            user += "\nNew questions:\n" + "".join(f"{i + 1}. {mask(h['question'])[:200]}\n" for i, h in enumerate(batch))
            try:
                r = llm().complete_json(CLUSTER_PROMPT, user, "gap_groups", CLUSTER_SCHEMA, model=REWRITE_MODEL,
                                        effort="none", max_tokens=200 + 60 * len(batch))
            except Exception as e:  # noqa: BLE001 - unsorted this time: asked again on the next list
                log.warning("gap groups not sorted: %s", e)
                continue
            titles = {g["group"]: {"ro": g["ro"].strip()[:MAX_TITLE], "ru": g["ru"].strip()[:MAX_TITLE]}
                      for g in r.data.get("new_groups") or []}
            for item in r.data.get("items") or []:
                n, label = item.get("n"), item.get("group") or ""
                if not isinstance(n, int) or not 1 <= n <= len(batch):
                    continue
                head = batch[n - 1]["answer_id"]
                if label in ids:
                    key = ids[label]
                else:  # a new group: its id is its first question's
                    key = ids.setdefault(label, head)
                    if key == head:
                        title = titles.get(label) or {}
                        out["title"][key] = title
                        existing.append({"id": key, "topic": item.get("topic") or "other",
                                         "title": title.get("ro") or mask(batch[n - 1]["question"])})
                out["group"][head] = key
                out["topic"][head] = item.get("topic") if item.get("topic") in TOPICS else "other"
        return out

    return cluster


class PgGaps:
    def __init__(self, pool: ConnectionPool, embed: Callable[[list[str]], np.ndarray] | None = None,
                 cluster: Cluster | None = None):
        from .suggestions import default_embed

        self.pool = pool
        self.embed = embed or default_embed
        self.cluster = cluster

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def rows(self, statuses: list[str], lang: str | None, days: int) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        return self._rows(
            "SELECT answer_id, question, lang, status, created_at, missing, retrieved_sites, gap_hidden, recheck, topic, "
            "answer, gap_group, gap_title "
            "FROM answers WHERE status = ANY(%s) AND created_at >= %s AND (%s::text IS NULL OR lang = %s) "
            "ORDER BY created_at", (statuses, since, lang, lang))

    def list(self, statuses: list[str], lang: str | None, days: int, limit: int, hidden: bool) -> dict:
        return gaps(self.rows(statuses, lang, days), self.embed, hidden=hidden, limit=limit,
                    cluster=self.sort_and_store if self.cluster else None)

    def sort_and_store(self, new: list[dict], existing: list[dict]) -> dict:
        """The model's sorting of new wording groups, kept so each is sorted once."""
        assert self.cluster is not None
        found = self.cluster(new, existing)
        for head, key in found["group"].items():
            self._rows("UPDATE answers SET gap_group = %s, topic = %s WHERE answer_id = %s",
                       (key, found["topic"].get(head), head))
        for key, title in found["title"].items():
            self._rows("UPDATE answers SET gap_title = %s WHERE answer_id = %s", (Jsonb(title), key))
        return found

    def members(self, gap_id: str) -> list[dict]:
        """The group a gap id stands for, grouped the same way (as stored) over every unanswered question."""
        rows = self.rows(["not_found", "partial"], None, 3650)
        return next((members for key, members, _ in merged(rows, self.embed) if key == gap_id), [])

    def hide(self, gap_id: str, hidden: bool) -> bool:
        members = self.members(gap_id)
        if members:
            self._rows("UPDATE answers SET gap_hidden = %s WHERE answer_id = ANY(%s)",
                       (hidden, [m["answer_id"] for m in members]))
        return bool(members)

    def recheck(self, gap_id: str, answer: Callable[[AskRequest], AskResponse]) -> dict | None:
        """The group's first question asked again through the normal answer pipeline: one question, one model call."""
        members = self.members(gap_id)
        if not members:
            return None
        head = next((m for m in members if m["answer_id"] == gap_id), members[0])
        r = answer(AskRequest(question=head["question"], lang=head["lang"]))
        result = {"status": r.status, "verified": r.meta.verified, "answer_id": r.id,
                  "ts": datetime.now(UTC).isoformat(timespec="seconds")}
        self._rows("UPDATE answers SET recheck = %s WHERE answer_id = %s", (Jsonb(result), gap_id))
        return result
