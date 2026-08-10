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
