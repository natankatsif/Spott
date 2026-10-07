"""When each site is checked (docs/history/audit/06-freshness-plan.md): every night a `check` of what changed, once a week a
full `refresh`, earlier when people signal outdated content (the backend moves next_check_at up).

Only sites that were processed at least once are scheduled: a source the admin added but never ran is a decision
for the admin, not for the night. Sites are spread over the night window, a few minutes apart, and one runs at a
time anyway (one worker)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


ENABLED = _flag("AUTO_UPDATE")
# The autopilot: when the worker has nothing queued, it takes the source with the most work left and does one
# bounded batch of it (worker/core.py: backlog_plan). Repeated batch after batch, every source is crawled,
# downloaded, parsed and indexed to the end without anyone pressing a button.
BACKLOG = _flag("AUTO_BACKLOG")
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


@dataclass
class SiteWork:
    """What a source still has left to do, as counted in the registry and the crawler's saved queue."""

    id: int
    site_id: str
    queue_left: int = 0     # pages the last crawl of it did not get to (its own cap, a cancel, a restart)
    undownloaded: int = 0   # documents found but never fetched
    files_pending: int = 0  # downloaded files not parsed yet
    pages_pending: int = 0  # crawled pages not parsed yet

    @property
    def total(self) -> int:
        return self.queue_left + self.undownloaded + self.files_pending + self.pages_pending

    def summary(self) -> str:
        parts = [f"{n} {name}" for n, name in (
            (self.queue_left, "pages to crawl"), (self.undownloaded, "to download"),
            (self.files_pending, "files to parse"), (self.pages_pending, "pages to parse")) if n]
        return ", ".join(parts) or "nothing left"


def next_backlog(works: list[SiteWork]) -> SiteWork | None:
    """The source the autopilot takes next: the one with the most left to do, the lowest id breaking a tie.
    None when every source is finished — then the worker just waits for the nightly checks."""
    return max((w for w in works if w.total > 0), key=lambda w: (w.total, -w.id), default=None)
