# Task 7 · Model và repository Appointment

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/models/appointment.py`, `app/repositories/appointment.py`, `tests/test_appointment_repository.py`

**Interfaces:**
- Consumes: `slot_keys_for` (Task 3), `SlotTakenError` (Task 1), `ensure_indexes` (Task 4)
- Produces:
  - `Appointment(user_id, user_name, phone, start_at, duration_minutes, slot_keys, note, status, created_via)`
  - `AppointmentRepository.insert_booked(...) -> Appointment` — ném `SlotTakenError` khi đụng unique index
  - `AppointmentRepository.find_active_at(user_id: str, start_at: datetime) -> Appointment | None`
  - `AppointmentRepository.cancel(appointment_id: str) -> bool`
  - `AppointmentRepository.upcoming_for_user(user_id: str, now: datetime) -> list[Appointment]`
  - `AppointmentRepository.booked_between(start: datetime, end: datetime) -> list[Appointment]`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_appointment_repository.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_appointment_repository.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.repositories.appointment'`

- [ ] **Step 3: Viết `app/models/appointment.py`**

```python
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field

from app.models.base import BaseDocument

Status = Literal["booked", "cancelled"]
CreatedVia = Literal["chat", "admin"]


class Appointment(BaseDocument):
    """Một lần hẹn. user_name và phone được chụp lại lúc đặt để admin đọc
    danh sách mà không phải join sang users."""

    user_id: str
    user_name: Optional[str] = None
    phone: Optional[str] = None
    start_at: datetime
    duration_minutes: int
    slot_keys: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    status: Status = "booked"
    created_via: CreatedVia = "chat"
```

- [ ] **Step 4: Viết `app/repositories/appointment.py`**

```python
from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.errors import SlotTakenError
from app.core.slots import slot_keys_for
from app.models.appointment import Appointment, CreatedVia
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Appointment, "appointments")

    async def insert_booked(
        self,
        user_id: str,
        user_name: Optional[str],
        phone: Optional[str],
        start_at: datetime,
        duration_minutes: int,
        note: Optional[str],
        created_via: CreatedVia,
    ) -> Appointment:
        """Ghi lịch mới. Chặn trùng do chính MongoDB làm qua unique partial index
        trên slot_keys — nguyên tử, không cần transaction, không cần khóa."""
        try:
            return await self.create({
                "user_id": user_id,
                "user_name": user_name,
                "phone": phone,
                "start_at": start_at,
                "duration_minutes": duration_minutes,
                "slot_keys": slot_keys_for(start_at, duration_minutes),
                "note": note,
                "status": "booked",
                "created_via": created_via,
            })
        except DuplicateKeyError as exc:
            raise SlotTakenError() from exc

    async def find_active_at(self, user_id: str, start_at: datetime) -> Optional[Appointment]:
        doc = await self.collection.find_one(
            {"user_id": user_id, "start_at": start_at, "status": "booked"}
        )
        return Appointment(**doc) if doc else None

    async def cancel(self, appointment_id: str) -> bool:
        """Chỉ đổi status. Document tự rơi khỏi partial index nên slot được giải
        phóng ngay, còn slot_keys giữ nguyên để tra cứu lịch sử."""
        if not ObjectId.is_valid(appointment_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(appointment_id), "status": "booked"},
            {"$set": {"status": "cancelled"}},
        )
        return result.modified_count == 1

    async def upcoming_for_user(self, user_id: str, now: datetime) -> List[Appointment]:
        docs = await self.collection.find({
            "user_id": user_id, "status": "booked", "start_at": {"$gte": now},
        }).sort("start_at", 1).to_list(length=100)
        return [Appointment(**d) for d in docs]

    async def booked_between(self, start: datetime, end: datetime) -> List[Appointment]:
        docs = await self.collection.find({
            "status": "booked", "start_at": {"$gte": start, "$lt": end},
        }).sort("start_at", 1).to_list(length=500)
        return [Appointment(**d) for d in docs]
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_appointment_repository.py -v`
Expected: PASS (10 passed) — đặc biệt `test_two_simultaneous_bookings_only_one_wins` và `test_cancelling_two_appointments_does_not_raise_duplicate_key`

- [ ] **Step 6: Commit**

```bash
git add app/models/appointment.py app/repositories/appointment.py tests/test_appointment_repository.py
git commit -m "feat: appointments with atomic slot-key conflict prevention"
```

---
