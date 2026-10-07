"""The tokens of every model call, for admin → Spending (spott.api.admin.spending prices them).

Every call through the routed LLM (the answer, the routing / rewrite / translation calls, the gap groups) leaves one
row in `llm_usage`: when, which provider and model, which role and which kind of call, input and output tokens and
how long it took. The row is written by a background thread, so a question never waits for it.
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)
TZ = ZoneInfo(os.getenv("USAGE_TZ") or "Europe/Chisinau")  # where "today" and "this month" start


@dataclass
class Call:
    provider: str
    model: str
    role: str       # answer | fast | deep (which role's model was used)
    kind: str       # the call's schema name: answer, route, rewrite, translate_quotes, translate, gap_groups...
    input_tokens: int
    output_tokens: int
    ms: int
    at: datetime | None = None


@dataclass
class Group:
    """Calls summed by day (in TZ), provider, model and kind."""
    day: date
    provider: str
    model: str
    kind: str
    calls: int
    input_tokens: int
    output_tokens: int


class UsageLog(Protocol):
    def record(self, call: Call) -> None: ...
    def groups(self, since: datetime | None) -> list[Group]: ...
    def questions(self, since: datetime | None) -> int: ...


class PgUsage:
    """Writes in a background thread (a queue, a few rows per insert); reads group in SQL."""

    def __init__(self, pool: ConnectionPool, max_queue: int = 10_000):
        self.pool = pool
        self.queue: queue.Queue[Call] = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._writer, name="llm-usage", daemon=True)
        self.thread.start()

    def record(self, call: Call) -> None:
        call.at = call.at or datetime.now(TZ)
        try:
            self.queue.put_nowait(call)
        except queue.Full:  # the database is down for a long time: lose the oldest accounting, not the answers
            log.warning("llm_usage queue full, a call's tokens not recorded")

    def _writer(self) -> None:
        while True:
            batch = [self.queue.get()]
            while len(batch) < 100:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            for attempt in range(3):
                try:
                    with self.pool.connection() as conn, conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO llm_usage (created_at, provider, model, role, kind, input_tokens, "
                            "output_tokens, ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            [(c.at, c.provider, c.model, c.role, c.kind, c.input_tokens, c.output_tokens, c.ms)
                             for c in batch])
                    break
                except Exception as e:  # noqa: BLE001 - accounting must never take the service down
                    log.warning("llm_usage write failed (%s), try %d", e, attempt + 1)
                    time.sleep(2 * (attempt + 1))

    def groups(self, since: datetime | None) -> list[Group]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT (created_at AT TIME ZONE %s)::date AS day, provider, model, kind, COUNT(*), "
                "COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM llm_usage "
                "WHERE %s::timestamptz IS NULL OR created_at >= %s GROUP BY 1, 2, 3, 4",
                (str(TZ), since, since)).fetchall()
        return [Group(*r) for r in rows]

    def questions(self, since: datetime | None) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM answers WHERE %s::timestamptz IS NULL OR created_at >= %s",
                               (since, since)).fetchone()
        return row[0] if row else 0
