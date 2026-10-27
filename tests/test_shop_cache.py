"""Cache Redis cho trạng thái/giờ mở cửa tiệm (Task 11).

Quy tắc controller: cache DỮ LIỆU THÔ (document Mongo), không cache view đã
tính (`minutes_left` tính từ `busy_until` sẽ cũ dần nếu cache luôn cả nó) —
test `test_minutes_left_keeps_counting_down_while_served_from_cache` chốt
đúng quy tắc này.
"""
import json
from datetime import timedelta

import pytest

from app.core.clock import now_utc
from app.infrastructure import redis as redis_module
from app.infrastructure.redis import cache_set
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
    fake_redis.store.clear()  # giả lập cache nguội, chỉ Mongo có dữ liệu

    calls = {"n": 0}
    real_find_one = svc.hours.find_one

    async def counting_find_one(*args, **kwargs):
        calls["n"] += 1
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(svc.hours, "find_one", counting_find_one)

    await svc.get_hours()
    await svc.get_hours()

    assert calls["n"] == 1


async def test_set_busy_writes_through_new_status_without_mongo_reload(test_db, fake_redis, monkeypatch):
    """Review fix: `set_busy`/`set_free` GHI THẲNG bản vừa lưu vào cache thay
    vì xoá rồi để lần đọc sau tự nạp lại Mongo — tránh khe hở một
    `get_status()` khác đọc xong Mongo (bản CŨ) trước lần ghi này nhưng hoàn
    tất SAU khi cache đã bị xoá, hồi sinh cache bằng dữ liệu cũ tới hết TTL."""
    svc = ShopService(test_db)

    calls = {"n": 0}
    real_find_one = svc.status.find_one

    async def counting_find_one(*args, **kwargs):
        calls["n"] += 1
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(svc.status, "find_one", counting_find_one)

    status = await svc.set_busy(30)
    assert status.is_busy is True
    cached = json.loads(fake_redis.store["shop:status"])
    assert cached["is_busy"] is True
    assert cached["busy_until"] is not None

    # get_status() ngay sau đó đọc được từ cache vừa ghi thẳng — không cần
    # Mongo (find_one chưa bị gọi lần nào).
    again = await svc.get_status()
    assert again.is_busy is True
    assert calls["n"] == 0

    freed = await svc.set_free()
    assert freed.is_busy is False
    cached = json.loads(fake_redis.store["shop:status"])
    assert cached == {"is_busy": False, "busy_until": None}
    assert calls["n"] == 0


async def test_set_hours_writes_through_new_hours_without_mongo_reload(test_db, fake_redis, monkeypatch):
    svc = ShopService(test_db)

    calls = {"n": 0}
    real_find_one = svc.hours.find_one

    async def counting_find_one(*args, **kwargs):
        calls["n"] += 1
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(svc.hours, "find_one", counting_find_one)

    await svc.set_hours("09:00", "18:00", [0])
    cached = json.loads(fake_redis.store["shop:hours"])
    assert cached == {"open_time": "09:00", "close_time": "18:00", "closed_days": [0]}

    hours = await svc.get_hours()
    assert hours.open_time == "09:00"
    assert calls["n"] == 0

    await svc.set_hours("10:00", "17:00", [1])
    hours = await svc.get_hours()
    assert hours.open_time == "10:00"
    assert calls["n"] == 0


async def test_admin_write_landing_last_wins_over_a_stale_concurrent_load(test_db, fake_redis):
    """Giả lập interleaving: một `get_status()` KHÁC đọc Mongo lúc tiệm còn
    rảnh rồi tự ghi cache (loader của `cache_get_or_load`), nhưng lần ghi đó
    TỚI cache TRƯỚC lần ghi thẳng của `set_busy()` — chiều hay gặp nhất, vì
    `set_busy()` vừa ghi Mongo xong nên tới sau. Lần ghi thẳng tới SAU CÙNG
    nên thắng: cache phản ánh đúng trạng thái bận.

    Chiều NGƯỢC LẠI (lần ghi của bản đọc cũ tới SAU lần ghi thẳng của
    `set_busy()`) vẫn có thể hồi sinh dữ liệu cũ tới hết TTL (30s) — khe hở dư
    đã chấp nhận, xem docstring `ShopService._write_through_status`; không
    assert nó biến mất ở đây."""
    svc = ShopService(test_db)
    stale_raw = {"is_busy": False, "busy_until": None}
    # Bản đọc cũ (từ trước khi set_busy chạy) "hoàn tất" và tự ghi cache trước.
    await cache_set("shop:status", stale_raw, 30, encode=json.dumps)

    status = await svc.set_busy(30)  # ghi Mongo rồi ghi thẳng cache — tới sau cùng

    assert status.is_busy is True
    cached = json.loads(fake_redis.store["shop:status"])
    assert cached["is_busy"] is True
    assert cached["busy_until"] is not None

    read_back = await svc.get_status()
    assert read_back.is_busy is True


async def test_write_through_redis_error_does_not_raise(test_db, fake_redis):
    """Lỗi Redis lúc ghi thẳng (sau khi Mongo đã ghi xong) chỉ đáng một dòng
    warning — `set_busy` vẫn phải trả view đúng, không ném lỗi ra ngoài."""
    fake_redis.fail = True
    svc = ShopService(test_db)

    status = await svc.set_busy(30)

    assert status.is_busy is True
    assert 28 <= status.minutes_left <= 30


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
