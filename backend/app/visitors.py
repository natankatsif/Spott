"""Unique visitors (POST /api/visits, Postgres table `visitors`): the counter in the header.

A visitor is a browser: the frontend sends its anonymous localStorage id (lib/session.ts), counted once however
many pages it opens. No IP, user agent or anything else is stored.
"""

from typing import Protocol

from psycopg_pool import ConnectionPool


class Visitors(Protocol):
    def visit(self, visitor_id: str) -> int: ...


class PgVisitors:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def visit(self, visitor_id: str) -> int:
        """Records the visitor if new; the number of unique visitors so far."""
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO visitors (visitor_id) VALUES (%s) ON CONFLICT DO NOTHING", (visitor_id,))
            cur.execute("SELECT COUNT(*) FROM visitors")
            return cur.fetchone()[0]
