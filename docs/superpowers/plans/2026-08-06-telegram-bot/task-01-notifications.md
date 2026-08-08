# Task 1 · Chỗ tỏa tin duy nhất

> Thuộc plan [Bot Telegram](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/services/notifications.py`, `tests/test_notifications.py`

**Interfaces:**
- Consumes: `Appointment`, `ShopStatusView` (Plan 1)
- Produces:
  - `Notifier` (Protocol): `appointment_created(appt)`, `appointment_cancelled(appt)`, `shop_status_changed(status)`
  - `NotificationService.register(channel: Notifier) -> None`
  - `NotificationService.appointment_created(appt) -> None`
  - `NotificationService.appointment_cancelled(appt) -> None`
  - `NotificationService.shop_status_changed(status) -> None`
  - `notifications` — instance dùng chung toàn app

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_notifications.py`:

```python
from datetime import datetime, timezone

import pytest

from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.notifications import NotificationService


class SpyChannel:
    def __init__(self, broken=False):
        self.broken = broken
        self.calls = []

    async def appointment_created(self, appt):
        self.calls.append(("created", appt.note))
        if self.broken:
            raise RuntimeError("kênh hỏng")

    async def appointment_cancelled(self, appt):
        self.calls.append(("cancelled", appt.note))

    async def shop_status_changed(self, status):
        self.calls.append(("status", status.is_busy))
        if self.broken:
            raise RuntimeError("kênh hỏng")


def an_appointment():
    return Appointment(
        user_id="u1", user_name="Cô Lan", phone="0912345678",
        start_at=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
        duration_minutes=60, note="làm tóc",
    )


pytestmark = pytest.mark.asyncio


async def test_every_registered_channel_receives_the_event():
    service = NotificationService()
    a, b = SpyChannel(), SpyChannel()
    service.register(a)
    service.register(b)

    await service.appointment_created(an_appointment())

    assert a.calls == [("created", "làm tóc")]
    assert b.calls == [("created", "làm tóc")]


async def test_a_broken_channel_does_not_stop_the_others():
    """Telegram chết không được ngăn Socket.IO, và tuyệt đối không được làm
    hỏng việc đặt lịch của khách."""
    service = NotificationService()
    broken, healthy = SpyChannel(broken=True), SpyChannel()
    service.register(broken)
    service.register(healthy)

    await service.appointment_created(an_appointment())

    assert healthy.calls == [("created", "làm tóc")]


async def test_no_channels_registered_is_not_an_error():
    await NotificationService().appointment_created(an_appointment())


async def test_shop_status_reaches_channels():
    service = NotificationService()
    spy = SpyChannel()
    service.register(spy)

    await service.shop_status_changed(ShopStatusView(is_busy=True, minutes_left=30))
    assert spy.calls == [("status", True)]


async def test_cancellation_reaches_channels():
    service = NotificationService()
    spy = SpyChannel()
    service.register(spy)

    await service.appointment_cancelled(an_appointment())
    assert spy.calls == [("cancelled", "làm tóc")]


async def test_channels_can_be_cleared_between_tests():
    service = NotificationService()
    service.register(SpyChannel())
    service.clear()
    assert service.channels == []
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_notifications.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.services.notifications'`

- [ ] **Step 3: Viết `app/services/notifications.py`**

```python
from typing import List, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView

logger = get_logger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Một kênh báo tin cho chủ tiệm. Socket.IO và Telegram đều cài giao diện này."""

    async def appointment_created(self, appointment: Appointment) -> None: ...
    async def appointment_cancelled(self, appointment: Appointment) -> None: ...
    async def shop_status_changed(self, status: ShopStatusView) -> None: ...


class NotificationService:
    """Cửa ra duy nhất để báo cho chủ tiệm.

    Nghiệp vụ không bao giờ gọi thẳng Socket.IO hay Telegram. Đi vòng qua đây là
    cách duy nhất giữ hai kênh không lệch nhau — và cũng là chỗ nuốt lỗi tập trung.
    """

    def __init__(self) -> None:
        self.channels: List[Notifier] = []

    def register(self, channel: Notifier) -> None:
        self.channels.append(channel)

    def clear(self) -> None:
        self.channels = []

    async def _fan_out(self, method: str, payload) -> None:
        for channel in self.channels:
            try:
                await getattr(channel, method)(payload)
            except Exception as exc:
                logger.warning(
                    "notification_channel_failed",
                    extra={"channel": type(channel).__name__, "method": method,
                           "error": str(exc)},
                )

    async def appointment_created(self, appointment: Appointment) -> None:
        await self._fan_out("appointment_created", appointment)

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self._fan_out("appointment_cancelled", appointment)

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        await self._fan_out("shop_status_changed", status)


notifications = NotificationService()
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_notifications.py -v`
Expected: PASS (6 passed) — quan trọng nhất là `test_a_broken_channel_does_not_stop_the_others`

- [ ] **Step 5: Commit**

```bash
git add app/services/notifications.py tests/test_notifications.py
git commit -m "feat: single fan-out point for owner notifications"
```
