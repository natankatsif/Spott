"""People's signals that a site's content is outdated (docs/history/audit/06-freshness-plan.md): a cited passage the
preview can't find on the live page any more, a dislike with the reason "outdated". Each moves the site's next
check earlier, but not sooner than 6 hours after its last one (a burst of dislikes must not hammer a site); the
worker's scheduler queues the check."""

from psycopg_pool import ConnectionPool

MIN_HOURS_BETWEEN_CHECKS = 6

SIGNAL_SQL = f"""
UPDATE sources SET stale_signals = stale_signals + 1, last_signal_at = NOW(),
       next_check_at = LEAST(COALESCE(next_check_at, 'infinity'),
                             GREATEST(NOW(), COALESCE(last_checked_at, '-infinity') + INTERVAL '{MIN_HOURS_BETWEEN_CHECKS} hours'))
WHERE kind = 'site' AND site_id = ANY(%s) AND enabled AND auto_update AND robots = 'allowed'
RETURNING site_id
"""


def signal_documents(pool: ConnectionPool, doc_ids: list[str]) -> list[str]:
    """Counts a signal for the sites of these documents; returns the sites signalled."""
    if not doc_ids:
        return []
    with pool.connection() as conn:
        sites = [r[0] for r in conn.execute("SELECT DISTINCT site FROM documents WHERE doc_id = ANY(%s) AND site IS NOT NULL",
                                            (list(doc_ids),)).fetchall()]
        if not sites:
            return []
        return [r[0] for r in conn.execute(SIGNAL_SQL, (sites,)).fetchall()]


def signal_answer(pool: ConnectionPool, answer_id: str) -> list[str]:
    """A dislike "outdated" of an answer: its cited documents' sites."""
    with pool.connection() as conn:
        row = conn.execute("SELECT doc_ids FROM answers WHERE answer_id = %s", (answer_id,)).fetchone()
    return signal_documents(pool, list(row[0] or [])) if row else []
