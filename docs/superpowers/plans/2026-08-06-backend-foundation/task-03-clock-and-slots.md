# Task 3 · Đồng hồ và lượng tử hóa slot

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/core/clock.py`, `app/core/slots.py`, `tests/test_slots.py`

**Interfaces:**
- Consumes: `settings` (Task 1), `AppError` (Task 1)
- Produces:
  - `app/core/clock.py`: `TZ: ZoneInfo`, `now_utc() -> datetime`, `to_local(dt: datetime) -> datetime`, `local_day_bounds(day: date) -> tuple[datetime, datetime]` (trả về mốc UTC đầu và cuối của một ngày địa phương)
  - `app/core/slots.py`: `SLOT_MINUTES: int = 15`, `quantize(dt: datetime) -> datetime` (làm tròn xuống bội số 15 phút), `slot_keys_for(start_at: datetime, duration_minutes: int) -> list[str]`, `MisalignedSlotError`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_slots.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.clock import TZ, local_day_bounds, to_local
from app.core.slots import (SLOT_MINUTES, MisalignedSlotError, quantize,
                            slot_keys_for)


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_slot_keys_cover_every_15_minutes_of_the_duration():
    assert slot_keys_for(utc(2026, 8, 7, 8), 60) == [
        "2026-08-07T08:00", "2026-08-07T08:15",
        "2026-08-07T08:30", "2026-08-07T08:45",
    ]


def test_slot_keys_for_a_single_slot():
    assert slot_keys_for(utc(2026, 8, 7, 8), 15) == ["2026-08-07T08:00"]


def test_slot_keys_span_midnight_correctly():
    assert slot_keys_for(utc(2026, 8, 7, 23, 45), 30) == [
        "2026-08-07T23:45", "2026-08-08T00:00",
    ]


def test_unaligned_start_is_rejected():
    with pytest.raises(MisalignedSlotError):
        slot_keys_for(utc(2026, 8, 7, 8, 7), 60)


def test_naive_datetime_is_rejected():
    with pytest.raises(MisalignedSlotError):
        slot_keys_for(datetime(2026, 8, 7, 8, 0), 60)


def test_quantize_rounds_down_to_the_slot_grid():
    assert quantize(utc(2026, 8, 7, 8, 14)) == utc(2026, 8, 7, 8, 0)
    assert quantize(utc(2026, 8, 7, 8, 15)) == utc(2026, 8, 7, 8, 15)


def test_local_day_bounds_covers_exactly_24_hours_in_vietnam_time():
    start, end = local_day_bounds(date(2026, 8, 7))
    assert end - start == timedelta(days=1)
    assert to_local(start).hour == 0
    assert to_local(start).date() == date(2026, 8, 7)


def test_timezone_is_vietnam():
    assert str(TZ) == "Asia/Ho_Chi_Minh"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_slots.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.core.clock'`

- [ ] **Step 3: Viết `app/core/clock.py`**

```python
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    """Thời điểm hiện tại, luôn có tzinfo. Mọi chỗ cần 'bây giờ' phải gọi hàm này."""
    return datetime.now(timezone.utc)


def to_local(dt: datetime) -> datetime:
    """Đổi sang giờ Việt Nam để hiển thị hoặc diễn giải ngôn ngữ tự nhiên."""
    return dt.astimezone(TZ)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Mốc UTC đầu và cuối của một ngày theo giờ Việt Nam.

    Dùng để truy vấn 'lịch hôm nay' mà không lệch múi giờ.
    """
    start_local = datetime.combine(day, time.min, tzinfo=TZ)
    return (
        start_local.astimezone(timezone.utc),
        (start_local + timedelta(days=1)).astimezone(timezone.utc),
    )
```

- [ ] **Step 4: Viết `app/core/slots.py`**

```python
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError

SLOT_MINUTES = 15
_KEY_FORMAT = "%Y-%m-%dT%H:%M"


class MisalignedSlotError(AppError):
    message = "Giờ đặt phải rơi đúng mốc 15 phút"


def quantize(dt: datetime) -> datetime:
    """Làm tròn xuống mốc 15 phút gần nhất."""
    dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=dt.minute - dt.minute % SLOT_MINUTES, second=0, microsecond=0)


def slot_keys_for(start_at: datetime, duration_minutes: int) -> list[str]:
    """Các mốc 15 phút mà một lịch chiếm.

    Đây là thứ được đặt unique partial index — MongoDB áp unique cho từng phần tử
    mảng xuyên document, nên hai lịch chồng giờ bị chính DB từ chối, nguyên tử,
    không cần transaction.
    """
    if start_at.tzinfo is None:
        raise MisalignedSlotError("Thiếu múi giờ")

    start = start_at.astimezone(timezone.utc)
    if start.minute % SLOT_MINUTES or start.second or start.microsecond:
        raise MisalignedSlotError()
    if duration_minutes <= 0 or duration_minutes % SLOT_MINUTES:
        raise MisalignedSlotError("Thời lượng phải là bội số 15 phút")

    count = duration_minutes // SLOT_MINUTES
    return [
        (start + timedelta(minutes=SLOT_MINUTES * i)).strftime(_KEY_FORMAT)
        for i in range(count)
    ]
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_slots.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: Commit**

```bash
git add app/core/clock.py app/core/slots.py tests/test_slots.py
git commit -m "feat: add timezone helpers and 15-minute slot quantisation"
```

---
