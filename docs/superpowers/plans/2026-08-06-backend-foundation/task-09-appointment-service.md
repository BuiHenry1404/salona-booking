# Task 9 · AppointmentService — đặt, hủy, gợi ý giờ trống

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/services/appointment.py`, `tests/test_appointment_service.py`

**Interfaces:**
- Consumes: `AppointmentRepository` (Task 7), `ShopService` (Task 8), `quantize`/`SLOT_MINUTES` (Task 3), `settings.booking_slot_minutes` (Task 1), các lớp lỗi (Task 1)
- Produces:
  - `AppointmentService(db).create(user: User, start_at: datetime, note: str | None, created_via: str = "chat") -> Appointment`
  - `AppointmentService(db).cancel(user: User, appointment_id: str) -> None` — ném `ForbiddenError` nếu không phải chủ lịch và không phải admin
  - `AppointmentService(db).upcoming_for(user: User) -> list[Appointment]`
  - `AppointmentService(db).day_schedule(day: date) -> list[Appointment]`
  - `AppointmentService(db).find_free_slots(day: date, limit: int = 12) -> list[datetime]`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_appointment_service.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError,
                             SlotTakenError)
from app.core.clock import TZ
from app.models.user import User
from app.services.appointment import AppointmentService

pytestmark = pytest.mark.asyncio


def make_user(phone="0912345678", name="Cô Lan", role="user"):
    """Mỗi lần gọi sinh một ObjectId mới, nên hai user khác nhau thật sự khác id.

    Quan trọng: đừng truyền id=None — khi đó str(user.id) là "None" cho mọi user,
    khóa idempotency sẽ gộp nhầm lịch của hai người thành một.
    """
    return User(phone=phone, hashed_password="h", full_name=name, role=role)


@pytest.fixture
def future_day():
    return date(2099, 8, 7)


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


async def test_create_stores_the_appointment(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note="làm tóc")
    assert appt.status == "booked"
    assert appt.note == "làm tóc"
    assert appt.user_name == "Cô Lan"
    assert appt.phone == "0912345678"


async def test_create_rejects_a_time_in_the_past(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(PastTimeError):
        await svc.create(make_user(), datetime.now(timezone.utc) - timedelta(hours=1), note=None)


async def test_create_rejects_a_time_outside_shop_hours(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(OutsideShopHoursError):
        await svc.create(make_user(), future_local(3), note=None)


async def test_create_rounds_the_time_down_to_the_slot_grid(test_db):
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15, 7), note=None)
    assert appt.start_at.astimezone(TZ).minute == 0


async def test_calling_create_twice_with_same_args_returns_the_same_appointment(test_db):
    """Khóa idempotency: AI gọi tool hai lần không được sinh hai lịch."""
    svc = AppointmentService(test_db)
    user = make_user()
    first = await svc.create(user, future_local(15), note="làm tóc")
    second = await svc.create(user, future_local(15), note="làm tóc")
    assert first.id == second.id


async def test_another_user_at_the_same_time_is_rejected(test_db):
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(15), note=None)
    other = make_user(phone="0938111222", name="Cô Hoa")
    with pytest.raises(SlotTakenError):
        await svc.create(other, future_local(15), note=None)


async def test_cancel_by_the_owner_succeeds(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    await svc.cancel(user, str(appt.id))
    assert await svc.upcoming_for(user) == []


async def test_cancel_by_someone_else_is_forbidden(test_db):
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15), note=None)

    intruder = make_user(phone="0938111222", name="Người lạ")
    with pytest.raises(ForbiddenError):
        await svc.cancel(intruder, str(appt.id))


async def test_admin_may_cancel_anyone_s_appointment(test_db):
    svc = AppointmentService(test_db)
    owner = make_user()
    appt = await svc.create(owner, future_local(15), note=None)

    boss = make_user(phone="0901234567", name="Chủ tiệm", role="admin")
    await svc.cancel(boss, str(appt.id))
    assert await svc.upcoming_for(owner) == []


async def test_cancel_unknown_id_raises_not_found(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(NotFoundError):
        await svc.cancel(make_user(), "64b7f0c2e4b0a1a2b3c4d5e6")


async def test_free_slots_stay_inside_shop_hours(test_db, future_day):
    svc = AppointmentService(test_db)
    slots = await svc.find_free_slots(future_day)
    assert slots
    for slot in slots:
        localised = slot.astimezone(TZ)
        assert 8 <= localised.hour < 19


async def test_free_slots_exclude_booked_times(test_db, future_day):
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(8), note=None)
    slots = await svc.find_free_slots(future_day)
    assert future_local(8) not in [s.astimezone(TZ) for s in slots]


async def test_free_slots_for_a_past_day_are_empty(test_db):
    svc = AppointmentService(test_db)
    assert await svc.find_free_slots(date(2020, 1, 1)) == []
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_appointment_service.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.services.appointment'`

- [ ] **Step 3: Viết cài đặt**

Tạo `app/services/appointment.py`:

```python
from datetime import date, datetime, timedelta
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import local_day_bounds, now_utc
from app.core.config import settings
from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError)
from app.core.slots import SLOT_MINUTES, quantize
from app.models.appointment import Appointment, CreatedVia
from app.models.user import User
from app.repositories.appointment import AppointmentRepository
from app.services.shop import ShopService

MAX_DAYS_AHEAD = 90


class AppointmentService:
    """Toàn bộ nghiệp vụ đặt lịch. Tool của agent, handler Telegram và REST cho
    React đều gọi lớp này — logic chỉ tồn tại một chỗ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AppointmentRepository(db)
        self.shop = ShopService(db)

    async def create(
        self,
        user: User,
        start_at: datetime,
        note: Optional[str],
        created_via: CreatedVia = "chat",
    ) -> Appointment:
        start = quantize(start_at)
        now = now_utc()

        if start < now:
            raise PastTimeError()
        if start > now + timedelta(days=MAX_DAYS_AHEAD):
            raise PastTimeError("Chỉ đặt được lịch trong vòng 3 tháng tới")
        if not await self.shop.is_open_at(start):
            raise OutsideShopHoursError()

        user_id = str(user.id)

        # Idempotency: AI gọi tool hai lần cho cùng một yêu cầu thì lần thứ hai
        # trả về chính lịch đã tạo, thay vì báo trùng cho chính khách vừa đặt.
        existing = await self.repo.find_active_at(user_id, start)
        if existing:
            return existing

        return await self.repo.insert_booked(
            user_id=user_id,
            user_name=user.full_name,
            phone=user.phone,
            start_at=start,
            duration_minutes=settings.booking_slot_minutes,
            note=note,
            created_via=created_via,
        )

    async def cancel(self, user: User, appointment_id: str) -> None:
        appt = await self.repo.get_by_id(appointment_id)
        if not appt or appt.status != "booked":
            raise NotFoundError("Không tìm thấy lịch này")
        if appt.user_id != str(user.id) and user.role != "admin":
            raise ForbiddenError("Chỉ hủy được lịch của chính mình")
        await self.repo.cancel(appointment_id)

    async def upcoming_for(self, user: User) -> List[Appointment]:
        return await self.repo.upcoming_for_user(str(user.id), now=now_utc())

    async def day_schedule(self, day: date) -> List[Appointment]:
        start, end = local_day_bounds(day)
        return await self.repo.booked_between(start, end)

    async def find_free_slots(self, day: date, limit: int = 12) -> List[datetime]:
        """Các mốc còn trống trong ngày: nằm trong giờ mở cửa, không trùng lịch
        đã có, và không ở quá khứ."""
        start, end = local_day_bounds(day)
        taken = {
            appt.start_at.replace(tzinfo=start.tzinfo)
            for appt in await self.repo.booked_between(start, end)
        }
        duration = settings.booking_slot_minutes
        now = now_utc()

        free: List[datetime] = []
        cursor = start
        while cursor < end and len(free) < limit:
            if cursor >= now and cursor not in taken and await self.shop.is_open_at(cursor):
                if not self._overlaps(cursor, duration, taken):
                    free.append(cursor)
            cursor += timedelta(minutes=SLOT_MINUTES)
        return free

    @staticmethod
    def _overlaps(candidate: datetime, duration: int, taken: set) -> bool:
        steps = duration // SLOT_MINUTES
        return any(
            candidate + timedelta(minutes=SLOT_MINUTES * i) in taken for i in range(steps)
        )
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_appointment_service.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/appointment.py tests/test_appointment_service.py
git commit -m "feat: appointment service with idempotency and free-slot search"
```

---
