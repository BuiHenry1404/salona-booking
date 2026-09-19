from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import SlotTakenError
from app.repositories.appointment import AppointmentRepository

pytestmark = pytest.mark.asyncio


def utc(h, mi=0, day=7):
    return datetime(2026, 8, day, h, mi, tzinfo=timezone.utc)


async def _book(repo, start, user_id="u1", minutes=60):
    return await repo.insert_booked(
        user_id=user_id, user_name="Cô Lan", phone="0912345678",
        start_at=start, duration_minutes=minutes, note="làm tóc", created_via="chat",
    )


async def test_booking_is_stored_with_slot_keys(test_db):
    repo = AppointmentRepository(test_db)
    appt = await _book(repo, utc(8))
    assert appt.status == "booked"
    assert appt.slot_keys == [
        "2026-08-07T08:00", "2026-08-07T08:15",
        "2026-08-07T08:30", "2026-08-07T08:45",
    ]


async def test_exact_same_time_is_rejected(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8))
    with pytest.raises(SlotTakenError):
        await _book(repo, utc(8), user_id="u2")


async def test_overlapping_time_is_rejected(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8))
    with pytest.raises(SlotTakenError):
        await _book(repo, utc(8, 30), user_id="u2")


async def test_adjacent_non_overlapping_time_is_allowed(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8))
    await _book(repo, utc(9), user_id="u2")


async def test_two_simultaneous_bookings_only_one_wins(test_db):
    """Chứng minh unique index chặn thật, không phải kiểm-tra-rồi-ghi."""
    import asyncio

    repo = AppointmentRepository(test_db)
    results = await asyncio.gather(
        _book(repo, utc(10), user_id="a"),
        _book(repo, utc(10), user_id="b"),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, SlotTakenError)]
    assert len(ok) == 1
    assert len(failed) == 1


async def test_cancelling_frees_the_slot(test_db):
    repo = AppointmentRepository(test_db)
    appt = await _book(repo, utc(8))
    assert await repo.cancel(str(appt.id)) is True
    await _book(repo, utc(8), user_id="u2")


async def test_cancelling_two_appointments_does_not_raise_duplicate_key(test_db):
    """Hồi quy: mảng rỗng bị index thành undefined nên bản thiết kế đầu tiên sai.

    Partial index lọc status='booked' mới là cách đúng.
    """
    repo = AppointmentRepository(test_db)
    a = await _book(repo, utc(8))
    b = await _book(repo, utc(9))
    assert await repo.cancel(str(a.id)) is True
    assert await repo.cancel(str(b.id)) is True


async def test_cancelled_appointment_keeps_its_slot_keys_for_history(test_db):
    repo = AppointmentRepository(test_db)
    appt = await _book(repo, utc(8))
    await repo.cancel(str(appt.id))
    doc = await test_db["appointments"].find_one({"_id": appt.id})
    assert doc["slot_keys"] != []
    assert doc["status"] == "cancelled"


async def test_find_active_at_returns_only_own_booking(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8), user_id="u1")
    assert await repo.find_active_at("u1", utc(8)) is not None
    assert await repo.find_active_at("u2", utc(8)) is None


async def test_upcoming_excludes_past_and_cancelled(test_db):
    repo = AppointmentRepository(test_db)
    past = await _book(repo, utc(8, day=6), user_id="u1")
    future = await _book(repo, utc(8, day=8), user_id="u1")
    cancelled = await _book(repo, utc(10, day=8), user_id="u1")
    await repo.cancel(str(cancelled.id))

    upcoming = await repo.upcoming_for_user("u1", now=utc(12, day=7))
    assert [a.id for a in upcoming] == [future.id]


async def test_distinct_customers_counts_each_user_once_per_month(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=8), user_id="u1")
    await _book(repo, utc(10, day=9), user_id="u1")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 1


async def test_distinct_customers_counts_two_different_users(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=7), user_id="u2")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 2


async def test_distinct_customers_ignores_cancelled(test_db):
    repo = AppointmentRepository(test_db)
    appt = await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=7), user_id="u2")
    await repo.cancel(str(appt.id))

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 1


async def test_distinct_customers_returns_no_key_for_month_without_bookings(test_db):
    repo = AppointmentRepository(test_db)
    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts == {}


async def test_distinct_customers_groups_by_vietnam_month_boundary(test_db):
    # 2026-08-31T18:00Z = 2026-09-01 01:00 giờ VN -> phải thuộc tháng 9.
    repo = AppointmentRepository(test_db)
    await _book(repo, datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc), user_id="u1")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assert counts == {"2026-09": 1}
