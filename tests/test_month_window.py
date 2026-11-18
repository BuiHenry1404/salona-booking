from datetime import datetime, timezone

from app.core.clock import TZ, month_window_bounds, recent_month_keys


def test_recent_month_keys_returns_twelve_chronological_months():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    keys = recent_month_keys(12, now)
    assert len(keys) == 12
    assert keys == sorted(keys)
    assert keys[0] == "2025-09"
    assert keys[-1] == "2026-08"


def test_recent_month_keys_crosses_year_boundary():
    now = datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)
    assert recent_month_keys(12, now)[0] == "2025-02"


def test_month_window_bounds_starts_at_vn_midnight_of_first_month():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    start, end = month_window_bounds(12, now)
    assert start.astimezone(TZ) == datetime(2025, 9, 1, 0, 0, tzinfo=TZ)
    assert end.astimezone(TZ) == datetime(2026, 9, 1, 0, 0, tzinfo=TZ)


def test_month_window_bounds_end_is_exclusive_and_covers_whole_current_month():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    start, end = month_window_bounds(12, now)
    last_moment = datetime(2026, 8, 31, 23, 59, tzinfo=TZ).astimezone(timezone.utc)
    assert start <= last_moment < end
