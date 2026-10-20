from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import TZ
from app.repositories.appointment import AppointmentRepository
from app.services.stats import StatsService

pytestmark = pytest.mark.asyncio


async def _book(db, start, user_id="u1"):
    return await AppointmentRepository(db).insert_booked(
        user_id=user_id, user_name="Cô Lan", phone="0912345678",
        start_at=start, duration_minutes=60, note=None, created_via="chat",
    )


def _this_month_at(day: int, hour: int) -> datetime:
    """Một thời điểm trong THÁNG HIỆN TẠI theo giờ VN, trả về dạng UTC."""
    local = datetime.now(TZ).replace(
        day=day, hour=hour, minute=0, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc)


async def test_returns_exactly_twelve_months_when_db_is_empty(test_db):
    result = await StatsService(test_db).customers_by_month()
    assert len(result) == 12
    assert all(item["customer_count"] == 0 for item in result)


async def test_months_are_chronological_and_current_month_is_last(test_db):
    result = await StatsService(test_db).customers_by_month()
    months = [item["month"] for item in result]
    assert months == sorted(months)
    assert months[-1] == datetime.now(TZ).strftime("%Y-%m")


async def test_same_customer_booking_three_times_counts_once(test_db):
    await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(7, 8), user_id="u1")
    await _book(test_db, _this_month_at(8, 8), user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_two_customers_count_two(test_db):
    await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(6, 9), user_id="u2")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 2


async def test_customer_with_both_booked_and_cancelled_counts_once(test_db):
    appt = await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(7, 8), user_id="u1")
    await AppointmentRepository(test_db).cancel(str(appt.id))

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_only_cancelled_in_month_counts_zero(test_db):
    appt = await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await AppointmentRepository(test_db).cancel(str(appt.id))

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 0


async def test_future_booking_in_current_month_is_counted(test_db):
    # Metric là "khách CÓ lịch", không phải "khách đã đến" — lịch ngày mai
    # trong cùng tháng vẫn tính. Bỏ qua nếu ngày mai đã sang tháng khác.
    tomorrow_local = datetime.now(TZ) + timedelta(days=1)
    if tomorrow_local.month != datetime.now(TZ).month:
        pytest.skip("ngày mai đã sang tháng khác")
    start = tomorrow_local.replace(hour=8, minute=0, second=0, microsecond=0)
    await _book(test_db, start.astimezone(timezone.utc), user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_bookings_older_than_window_are_excluded(test_db):
    old = datetime.now(TZ).replace(day=1, hour=8, minute=0, second=0, microsecond=0)
    old = (old - timedelta(days=400)).astimezone(timezone.utc)
    await _book(test_db, old, user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert sum(item["customer_count"] for item in result) == 0
