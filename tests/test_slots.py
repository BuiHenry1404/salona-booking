from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.clock import TZ, local_day_bounds, to_local
from app.core.slots import (SLOT_MINUTES, MisalignedSlotError, quantize,
                            slot_keys_for)


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_slot_keys_cover_every_15_minutes_of_the_duration():
    assert slot_keys_for(utc(2026, 8, 7, 8), 60) == [
        "2026-08-07T08:00", "2026-08-07T08:15",
        "2026-08-07T08:30", "2026-08-07T08:45",
    ]


def test_slot_keys_for_a_single_slot():
    assert slot_keys_for(utc(2026, 8, 7, 8), 15) == ["2026-08-07T08:00"]


def test_slot_keys_span_midnight_correctly():
    assert slot_keys_for(utc(2026, 8, 7, 23, 45), 30) == [
        "2026-08-07T23:45", "2026-08-08T00:00",
    ]


def test_unaligned_start_is_rejected():
    with pytest.raises(MisalignedSlotError):
        slot_keys_for(utc(2026, 8, 7, 8, 7), 60)


def test_naive_datetime_is_rejected():
    with pytest.raises(MisalignedSlotError):
        slot_keys_for(datetime(2026, 8, 7, 8, 0), 60)


def test_quantize_rounds_down_to_the_slot_grid():
    assert quantize(utc(2026, 8, 7, 8, 14)) == utc(2026, 8, 7, 8, 0)
    assert quantize(utc(2026, 8, 7, 8, 15)) == utc(2026, 8, 7, 8, 15)


def test_local_day_bounds_covers_exactly_24_hours_in_vietnam_time():
    start, end = local_day_bounds(date(2026, 8, 7))
    assert end - start == timedelta(days=1)
    assert to_local(start).hour == 0
    assert to_local(start).date() == date(2026, 8, 7)


def test_timezone_is_vietnam():
    assert str(TZ) == "Asia/Ho_Chi_Minh"
