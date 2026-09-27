from datetime import UTC, datetime, timedelta

from worker.schedule import SourceState, decide, next_window

NOW = datetime(2026, 9, 27, 14, 30, tzinfo=UTC)


def s(i, **kw):
    return SourceState(**({"id": i, "next_check_at": None, "last_full_at": NOW - timedelta(days=1), "busy": False,
                          "processed": True} | kw))


def test_new_sources_get_a_slot_in_tonights_window_spread_apart():
    queue, next_at = decide([s(1), s(2), s(3)], NOW, hour_utc=0)
    assert queue == []
    assert next_at[1] == datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    assert next_at[2] - next_at[1] == timedelta(minutes=3) and next_at[3] - next_at[2] == timedelta(minutes=3)


def test_a_due_source_gets_a_check_and_a_weekly_one_a_full_refresh():
    queue, next_at = decide([s(1, next_check_at=NOW - timedelta(minutes=1)),
                             s(2, next_check_at=NOW, last_full_at=NOW - timedelta(days=8)),
                             s(3, next_check_at=NOW, last_full_at=None)], NOW, hour_utc=0)
    assert queue == [(1, "check"), (2, "refresh"), (3, "refresh")]
    assert all(t > NOW for t in next_at.values())


def test_busy_not_yet_due_and_never_processed_sources_are_left_alone():
    queue, next_at = decide([s(1, next_check_at=NOW - timedelta(hours=1), busy=True),
                             s(2, next_check_at=NOW + timedelta(hours=1)),
                             s(3, processed=False)], NOW)
    assert queue == [] and next_at == {}


def test_the_window_is_the_next_one_after_now():
    assert next_window(datetime(2026, 9, 27, 23, 59, tzinfo=UTC), 0, 0) == datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    assert next_window(datetime(2026, 9, 28, 0, 0, tzinfo=UTC), 0, 0) == datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
    assert next_window(NOW, 100, 0).minute == (100 * 3) % 240 % 60
