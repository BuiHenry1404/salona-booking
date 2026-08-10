from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import TZ
from app.services.shop import ShopService

pytestmark = pytest.mark.asyncio


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


async def test_default_hours_when_never_configured(test_db):
    hours = await ShopService(test_db).get_hours()
    assert hours.open_time == "08:00"
    assert hours.close_time == "19:00"
    assert hours.closed_days == []


async def test_set_and_read_hours(test_db):
    svc = ShopService(test_db)
    await svc.set_hours("09:00", "18:00", [0])
    hours = await svc.get_hours()
    assert (hours.open_time, hours.close_time, hours.closed_days) == ("09:00", "18:00", [0])


async def test_shop_starts_free(test_db):
    status = await ShopService(test_db).get_status()
    assert status.is_busy is False
    assert status.minutes_left is None


async def test_set_busy_reports_minutes_left(test_db):
    svc = ShopService(test_db)
    status = await svc.set_busy(30)
    assert status.is_busy is True
    assert 28 <= status.minutes_left <= 30


async def test_busy_expires_automatically_without_admin_action(test_db):
    svc = ShopService(test_db)
    await svc.set_busy(30)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await test_db["shop_status"].update_one({}, {"$set": {"busy_until": past}})

    status = await svc.get_status()
    assert status.is_busy is False
    assert status.minutes_left is None


async def test_set_free_clears_busy(test_db):
    svc = ShopService(test_db)
    await svc.set_busy(60)
    status = await svc.set_free()
    assert status.is_busy is False


async def test_is_open_inside_business_hours(test_db):
    svc = ShopService(test_db)
    assert await svc.is_open_at(local(2026, 8, 7, 10)) is True


async def test_is_closed_before_opening_and_after_closing(test_db):
    svc = ShopService(test_db)
    assert await svc.is_open_at(local(2026, 8, 7, 7, 30)) is False
    assert await svc.is_open_at(local(2026, 8, 7, 19, 0)) is False


async def test_is_closed_on_a_closed_day(test_db):
    svc = ShopService(test_db)
    await svc.set_hours("08:00", "19:00", [0])   # nghỉ Chủ nhật
    assert await svc.is_open_at(local(2026, 8, 9, 10)) is False   # 9/8/2026 là Chủ nhật
    assert await svc.is_open_at(local(2026, 8, 10, 10)) is True
