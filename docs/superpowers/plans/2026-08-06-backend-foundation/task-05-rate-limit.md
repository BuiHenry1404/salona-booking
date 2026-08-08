# Task 5 · Rate limit trên MongoDB

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/services/rate_limit.py`, `tests/test_rate_limit.py`

**Interfaces:**
- Consumes: `now_utc` (Task 3), `RateLimitedError` (Task 1)
- Produces: `RateLimitService(db).check_and_hit(key: str, limit: int = 5, window_seconds: int = 3600) -> None` — ném `RateLimitedError` khi vượt ngưỡng.

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_rate_limit.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_rate_limit.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.services.rate_limit'`

- [ ] **Step 3: Viết cài đặt**

Tạo `app/services/rate_limit.py`:

```python
from datetime import timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.core.errors import RateLimitedError


class RateLimitService:
    """Đếm số lần thử trong một cửa sổ thời gian, lưu ở Mongo.

    Không giữ trong bộ nhớ tiến trình: biến RAM mất khi restart và không dùng
    được nếu về sau chạy nhiều tiến trình. Stack không có Redis (xem README spec).
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["rate_limits"]

    async def check_and_hit(self, key: str, limit: int = 5, window_seconds: int = 3600) -> None:
        now = now_utc()
        window_start = now - timedelta(seconds=window_seconds)

        recent = await self.collection.count_documents(
            {"key": key, "created_at": {"$gte": window_start}}
        )
        if recent >= limit:
            raise RateLimitedError()

        await self.collection.insert_one({
            "key": key,
            "created_at": now,
            "expires_at": now + timedelta(seconds=window_seconds),
        })
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_rate_limit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/rate_limit.py tests/test_rate_limit.py
git commit -m "feat: add Mongo-backed rate limiter with TTL cleanup"
```

---
