import pytest

from app.core.errors import RateLimitedError
from app.services.rate_limit import RateLimitService

pytestmark = pytest.mark.asyncio


async def test_allows_up_to_the_limit(test_db):
    svc = RateLimitService(test_db)
    for _ in range(5):
        await svc.check_and_hit("phone:0912345678", limit=5, window_seconds=3600)


async def test_blocks_the_sixth_attempt(test_db):
    svc = RateLimitService(test_db)
    for _ in range(5):
        await svc.check_and_hit("phone:0912345678", limit=5, window_seconds=3600)
    with pytest.raises(RateLimitedError):
        await svc.check_and_hit("phone:0912345678", limit=5, window_seconds=3600)


async def test_different_keys_are_counted_separately(test_db):
    svc = RateLimitService(test_db)
    for _ in range(5):
        await svc.check_and_hit("phone:0912345678", limit=5, window_seconds=3600)
    await svc.check_and_hit("ip:1.2.3.4", limit=5, window_seconds=3600)


async def test_attempts_outside_the_window_do_not_count(test_db):
    from datetime import timedelta

    from app.core.clock import now_utc

    svc = RateLimitService(test_db)
    old = now_utc() - timedelta(hours=2)
    await test_db["rate_limits"].insert_many([
        {"key": "phone:0912345678", "created_at": old, "expires_at": old + timedelta(hours=1)}
        for _ in range(5)
    ])
    await svc.check_and_hit("phone:0912345678", limit=5, window_seconds=3600)


async def test_a_concurrent_burst_cannot_slip_past_the_limit(test_db):
    """Giữa count_documents và insert_one có một await, nên toàn bộ request
    đang chờ đều đọc thấy count cũ và cùng lọt qua. Uvicorn chạy 1 worker
    nhưng là async — đúng điều kiện để xảy ra chuyện đó.

    Đây là hàng rào duy nhất chặn script đổi mật khẩu hàng loạt (key theo IP),
    nên vỡ ở đây là vỡ đúng chỗ quan trọng.
    """
    import asyncio

    svc = RateLimitService(test_db)

    async def attempt():
        try:
            await svc.check_and_hit("pwreset:ip:1.2.3.4", limit=5, window_seconds=3600)
            return True
        except RateLimitedError:
            return False

    results = await asyncio.gather(*[attempt() for _ in range(20)])
    assert sum(results) <= 5, f"{sum(results)}/20 lọt qua giới hạn 5"


async def test_check_raises_once_the_limit_is_already_reached(test_db):
    svc = RateLimitService(test_db)
    for _ in range(3):
        await svc.hit("login:phone:0912345678", window_seconds=900)

    await svc.check("login:phone:0912345678", limit=4, window_seconds=900)

    with pytest.raises(RateLimitedError):
        await svc.check("login:phone:0912345678", limit=3, window_seconds=900)


async def test_check_does_not_record_a_hit_of_its_own(test_db):
    svc = RateLimitService(test_db)
    await svc.hit("login:ip:1.2.3.4", window_seconds=900)

    for _ in range(10):
        await svc.check("login:ip:1.2.3.4", limit=2, window_seconds=900)

    assert await test_db["rate_limits"].count_documents({"key": "login:ip:1.2.3.4"}) == 1
