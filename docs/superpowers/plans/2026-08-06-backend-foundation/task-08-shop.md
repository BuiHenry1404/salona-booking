# Task 8 · Giờ mở cửa và trạng thái bận/rảnh

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/models/shop.py`, `app/services/shop.py`, `tests/test_shop.py`

**Interfaces:**
- Consumes: `now_utc`, `to_local`, `TZ` (Task 3)
- Produces:
  - `ShopHours(open_time: str, close_time: str, closed_days: list[int])` — `closed_days` dùng quy ước 0 = Chủ nhật
  - `ShopStatusView(is_busy: bool, busy_until: datetime | None, minutes_left: int | None)`
  - `ShopService(db).get_hours() -> ShopHours`
  - `ShopService(db).set_hours(open_time, close_time, closed_days) -> ShopHours`
  - `ShopService(db).get_status() -> ShopStatusView`
  - `ShopService(db).set_busy(minutes: int) -> ShopStatusView`
  - `ShopService(db).set_free() -> ShopStatusView`
  - `ShopService(db).is_open_at(dt: datetime) -> bool`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_shop.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_shop.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.services.shop'`

- [ ] **Step 3: Viết `app/models/shop.py`**

```python
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ShopHours(BaseModel):
    """Giờ mở cửa. closed_days theo quy ước 0 = Chủ nhật ... 6 = Thứ Bảy."""

    open_time: str = "08:00"
    close_time: str = "19:00"
    closed_days: List[int] = Field(default_factory=list)


class ShopStatusView(BaseModel):
    """Trạng thái tiệm đã tính sẵn cho hiển thị và cho AI đọc."""

    is_busy: bool
    busy_until: Optional[datetime] = None
    minutes_left: Optional[int] = None
```

- [ ] **Step 4: Viết `app/services/shop.py`**

```python
import math
from datetime import datetime, timedelta
from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.models.shop import ShopHours, ShopStatusView

_SINGLETON = {"_id": "singleton"}


class ShopService:
    """Giờ mở cửa và trạng thái bận/rảnh. Cả hai là document đơn lẻ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.hours = db["shop_hours"]
        self.status = db["shop_status"]

    async def get_hours(self) -> ShopHours:
        doc = await self.hours.find_one(_SINGLETON)
        return ShopHours(**{k: v for k, v in (doc or {}).items() if k != "_id"})

    async def set_hours(self, open_time: str, close_time: str, closed_days: List[int]) -> ShopHours:
        hours = ShopHours(open_time=open_time, close_time=close_time, closed_days=closed_days)
        await self.hours.update_one(_SINGLETON, {"$set": hours.model_dump()}, upsert=True)
        return hours

    async def get_status(self) -> ShopStatusView:
        """Quá busy_until thì coi như rảnh mà không cần admin bấm lại —
        đây là cách hiện thực yêu cầu 'hết giờ tự về Rảnh'."""
        doc = await self.status.find_one(_SINGLETON) or {}
        busy_until = doc.get("busy_until")
        if not doc.get("is_busy") or busy_until is None:
            return ShopStatusView(is_busy=False)

        if busy_until.tzinfo is None:
            busy_until = busy_until.replace(tzinfo=now_utc().tzinfo)

        remaining = (busy_until - now_utc()).total_seconds()
        if remaining <= 0:
            return ShopStatusView(is_busy=False)

        return ShopStatusView(
            is_busy=True,
            busy_until=busy_until,
            minutes_left=max(1, math.ceil(remaining / 60)),
        )

    async def set_busy(self, minutes: int) -> ShopStatusView:
        until = now_utc() + timedelta(minutes=minutes)
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": True, "busy_until": until, "updated_at": now_utc()}},
            upsert=True,
        )
        return await self.get_status()

    async def set_free(self) -> ShopStatusView:
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": False, "busy_until": None, "updated_at": now_utc()}},
            upsert=True,
        )
        return await self.get_status()

    async def is_open_at(self, dt: datetime) -> bool:
        hours = await self.get_hours()
        local = to_local(dt)

        # Python: Monday=0 ... Sunday=6. Quy ước của ta: Sunday=0 ... Saturday=6.
        weekday = (local.weekday() + 1) % 7
        if weekday in hours.closed_days:
            return False

        minutes = local.hour * 60 + local.minute
        return _to_minutes(hours.open_time) <= minutes < _to_minutes(hours.close_time)


def _to_minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_shop.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: Commit**

```bash
git add app/models/shop.py app/services/shop.py tests/test_shop.py
git commit -m "feat: shop hours and auto-expiring busy status"
```

---
