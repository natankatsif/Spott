"""Answers given and their star ratings (Postgres tables `answers`, `feedback`).

Every answer is recorded with its question, status, cited documents and path, so a rating can be analysed
without the query logs, and quick questions (suggestions.py) come from what people really asked.
"""

import logging
from typing import Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .schemas import AskRequest, AskResponse, FeedbackRequest

log = logging.getLogger("backend.answers")


class Answers(Protocol):
    def record(self, req: AskRequest, resp: AskResponse) -> None: ...
    def rate(self, req: FeedbackRequest) -> bool: ...


def answer_row(req: AskRequest, resp: AskResponse) -> dict:
    return {"answer_id": resp.id, "session_id": req.session_id, "question": req.question, "lang": resp.lang,
            "status": resp.status, "verified": resp.meta.verified, "path": resp.meta.path,
            "citations": len(resp.citations), "doc_ids": list(dict.fromkeys(c.doc_id for c in resp.citations)),
            "answer": resp.answer}


class PgAnswers:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def record(self, req: AskRequest, resp: AskResponse) -> None:
        row = answer_row(req, resp)
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO answers (answer_id, session_id, question, lang, status, verified, path, citations, "
                    "doc_ids, answer) VALUES (%(answer_id)s, %(session_id)s, %(question)s, %(lang)s, %(status)s, "
                    "%(verified)s, %(path)s, %(citations)s, %(doc_ids)s, %(answer)s) ON CONFLICT DO NOTHING",
                    row | {"doc_ids": Jsonb(row["doc_ids"])})
        except Exception as e:  # recording must never break an answer
            log.warning("answer not recorded: %s", e)

    def rate(self, req: FeedbackRequest) -> bool:
        """Stores the rating with what was answered; the same answer rated again from the same session
        overwrites. False when the answer id is unknown."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM answers WHERE answer_id = %s", (req.answer_id,))
            answer = cur.fetchone()
            if answer is None:
                return False
            cur.execute(
                """
                INSERT INTO feedback (answer_id, session_id, rating, tags, comment, citation_id, question, lang,
                                      status, doc_ids, path, answer)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (answer_id, session_id) DO UPDATE SET
                    rating = EXCLUDED.rating, tags = EXCLUDED.tags, comment = EXCLUDED.comment,
                    citation_id = EXCLUDED.citation_id, updated_at = NOW()
                """,
                (req.answer_id, req.session_id or "", req.stars, Jsonb(list(dict.fromkeys(req.tags))), req.comment,
                 req.citation_id, answer["question"], answer["lang"], answer["status"], Jsonb(answer["doc_ids"]),
                 answer["path"], answer["answer"]))
        return True
