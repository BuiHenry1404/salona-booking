"""Cache Redis cho trạng thái/giờ mở cửa tiệm (Task 11).

Quy tắc controller: cache DỮ LIỆU THÔ (document Mongo), không cache view đã
tính (`minutes_left` tính từ `busy_until` sẽ cũ dần nếu cache luôn cả nó) —
test `test_minutes_left_keeps_counting_down_while_served_from_cache` chốt
đúng quy tắc này.
"""
from datetime import timedelta

import pytest

from app.core.clock import now_utc
from app.infrastructure import redis as redis_module
from app.services import shop as shop_module
from app.services.shop import ShopService

pytestmark = pytest.mark.asyncio


class FakeAsyncRedis:
    """Fake Redis tối giản cho test — chỉ get/set(ex=)/delete, không TTL thật
    (TTL là tham số ghi nhận được, không tự hết hạn trong test)."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.fail = False

    async def get(self, key):
        if self.fail:
            raise ConnectionError("fake redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("fake redis down")
        self.store[key] = value
        self.set_calls.append((key, value, ex))
        return True

    async def delete(self, key):
        if self.fail:
            raise ConnectionError("fake redis down")
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_redis_module():
    redis_module.reset_redis_for_tests()
    yield
    redis_module.reset_redis_for_tests()


@pytest.fixture
def fake_redis(monkeypatch):
    client = FakeAsyncRedis()
    monkeypatch.setattr(redis_module, "get_redis", lambda: client)
    return client


async def test_status_is_served_from_cache_within_ttl(test_db, fake_redis, monkeypatch):
    svc = ShopService(test_db)
    await svc.set_busy(30)
    fake_redis.store.clear()  # giả lập cache nguội, chỉ Mongo có dữ liệu

    calls = {"n": 0}
    real_find_one = svc.status.find_one

    async def counting_find_one(*args, **kwargs):
        calls["n"] += 1
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(svc.status, "find_one", counting_find_one)

    await svc.get_status()
    await svc.get_status()

    assert calls["n"] == 1


async def test_hours_is_served_from_cache_within_ttl(test_db, fake_redis, monkeypatch):
    svc = ShopService(test_db)
    await svc.set_hours("09:00", "18:00", [0])

    calls = {"n": 0}
    real_find_one = svc.hours.find_one

    async def counting_find_one(*args, **kwargs):
        calls["n"] += 1
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(svc.hours, "find_one", counting_find_one)

    await svc.get_hours()
    await svc.get_hours()

    assert calls["n"] == 1


async def test_changing_status_invalidates_cache(test_db, fake_redis):
    svc = ShopService(test_db)
    await svc.set_busy(30)
    await svc.get_status()
    assert "shop:status" in fake_redis.store

    await svc.set_free()

    status = await svc.get_status()
    assert status.is_busy is False
    # set_free() đã xoá cache cũ; đọc lại nạp bản mới (is_busy False) vào cache.
    assert "shop:status" in fake_redis.store


async def test_changing_hours_invalidates_cache(test_db, fake_redis):
    svc = ShopService(test_db)
    await svc.set_hours("09:00", "18:00", [0])
    await svc.get_hours()
    assert "shop:hours" in fake_redis.store

    await svc.set_hours("10:00", "17:00", [1])

    hours = await svc.get_hours()
    assert hours.open_time == "10:00"


async def test_cache_backend_error_reads_mongo(test_db, fake_redis):
    svc = ShopService(test_db)
    await svc.set_busy(45)
    fake_redis.fail = True

    status = await svc.get_status()

    assert status.is_busy is True
    assert status.minutes_left is not None


async def test_minutes_left_keeps_counting_down_while_served_from_cache(
    test_db, fake_redis, monkeypatch
):
    """Cache RAW busy_until, không cache ShopStatusView — minutes_left tính
    lại từ đồng hồ hiện tại mỗi lần đọc, kể cả khi đọc từ cache."""
    svc = ShopService(test_db)
    base = now_utc()
    monkeypatch.setattr(shop_module, "now_utc", lambda: base)
    await svc.set_busy(30)

    first = await svc.get_status()
    assert first.minutes_left == 30

    later = base + timedelta(minutes=10)
    monkeypatch.setattr(shop_module, "now_utc", lambda: later)
    second = await svc.get_status()

    assert second.minutes_left == 20
    assert second.is_busy is True


async def test_redis_disabled_behaves_like_today(test_db, monkeypatch):
    monkeypatch.setattr(redis_module, "get_redis", lambda: None)
    svc = ShopService(test_db)

    await svc.set_busy(30)
    status = await svc.get_status()

    assert status.is_busy is True
    assert 28 <= status.minutes_left <= 30
