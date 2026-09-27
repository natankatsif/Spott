"""When each site is checked (docs/audit/06-freshness-plan.md): every night a `check` of what changed, once a week a
full `refresh`, earlier when people signal outdated content (the backend moves next_check_at up).

Only sites that were processed at least once are scheduled: a source the admin added but never ran is a decision
for the admin, not for the night. Sites are spread over the night window, a few minutes apart, and one runs at a
time anyway (one worker)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

ENABLED = os.getenv("AUTO_UPDATE", "true").lower() in ("1", "true", "yes")
HOUR_UTC = int(os.getenv("AUTO_UPDATE_HOUR_UTC", "0"))  # 00:00 UTC = 03:00 in Chișinău in summer, 02:00 in winter
FULL_EVERY = timedelta(days=int(os.getenv("AUTO_FULL_REFRESH_DAYS", "7")))
SPREAD_MIN = 3  # minutes between two sites' slots in the window


@dataclass
class SourceState:
    id: int
    next_check_at: datetime | None
    last_full_at: datetime | None
    busy: bool  # a job of it is queued or running
    processed: bool  # it was processed at least once


def next_window(now: datetime, slot: int, hour_utc: int = HOUR_UTC) -> datetime:
    """This source's time in the next night window after `now`."""
    at = now.astimezone(UTC).replace(hour=hour_utc, minute=0, second=0, microsecond=0) + \
        timedelta(minutes=(slot * SPREAD_MIN) % 240)
    return at if at > now else at + timedelta(days=1)


def decide(sources: list[SourceState], now: datetime, hour_utc: int = HOUR_UTC,
           full_every: timedelta = FULL_EVERY) -> tuple[list[tuple[int, str]], dict[int, datetime]]:
    """(jobs to queue as (source id, kind), new next_check_at per source)."""
    queue, next_at = [], {}
    for slot, s in enumerate(sources):
        if not s.processed:
            continue
        if s.next_check_at is None:
            next_at[s.id] = next_window(now, slot, hour_utc)
        elif s.next_check_at <= now and not s.busy:
            full = s.last_full_at is None or now - s.last_full_at >= full_every
            queue.append((s.id, "refresh" if full else "check"))
            next_at[s.id] = next_window(now, slot, hour_utc)
    return queue, next_at
