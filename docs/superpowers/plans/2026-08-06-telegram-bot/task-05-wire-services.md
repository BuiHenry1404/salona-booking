# Task 5 · Nối thông báo vào nghiệp vụ

> Thuộc plan [Bot Telegram](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/telegram/notify.py`, `app/services/socket_notifier.py`, `tests/test_notification_wiring.py`
- Modify: `app/services/appointment.py`, `app/api/v1/routers/shop.py`, `main.py`

**Interfaces:**
- Consumes: `notifications` (task 1), `TelegramClient` (task 2), `SocketIOService` (Plan 2 task 10, nếu chưa có thì bỏ qua kênh Socket.IO)
- Produces:
  - `TelegramNotifier(client)` — cài `Notifier`
  - `SocketNotifier(socketio_service)` — cài `Notifier`
  - `AppointmentService.create/cancel` phát thông báo

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_notification_wiring.py`:

```python
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ
from app.models.shop import ShopStatusView
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.notifications import notifications
from app.telegram.notify import TelegramNotifier

pytestmark = pytest.mark.asyncio

ADMIN_ID = 111


class SpyClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text})


@pytest.fixture(autouse=True)
def clean_channels():
    notifications.clear()
    yield
    notifications.clear()


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: {ADMIN_ID})
    client = SpyClient()
    notifications.register(TelegramNotifier(client))
    return client


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def test_booking_notifies_the_owner(test_db, telegram):
    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")
    await AppointmentService(test_db).create(user, tomorrow_at(15), note="làm tóc")

    assert len(telegram.sent) == 1
    text = telegram.sent[0]["text"]
    assert telegram.sent[0]["chat_id"] == ADMIN_ID
    assert "Cô Lan" in text and "làm tóc" in text and "0912345678" in text


async def test_cancelling_notifies_the_owner(test_db, telegram):
    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")
    service = AppointmentService(test_db)
    appt = await service.create(user, tomorrow_at(15), note="làm tóc")
    telegram.sent.clear()

    await service.cancel(user, str(appt.id))
    assert "hủy" in telegram.sent[0]["text"].lower()


async def test_telegram_failure_does_not_break_booking(test_db, monkeypatch):
    """Đặt lịch phải thành công kể cả khi Telegram chết."""
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: {ADMIN_ID})

    class Broken:
        async def send_message(self, *a, **kw):
            raise RuntimeError("mạng chết")

    notifications.register(TelegramNotifier(Broken()))
    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")

    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), note=None)
    assert appt.status == "booked"


async def test_no_admin_chat_id_means_no_message(test_db, monkeypatch):
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: set())
    client = SpyClient()
    notifications.register(TelegramNotifier(client))

    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")
    await AppointmentService(test_db).create(user, tomorrow_at(15), note=None)
    assert client.sent == []


async def test_status_change_notifies_the_owner(telegram):
    await notifications.shop_status_changed(ShopStatusView(is_busy=True, minutes_left=30))
    assert "bận" in telegram.sent[0]["text"].lower()
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_notification_wiring.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.telegram.notify'`

- [ ] **Step 3: Viết `app/telegram/notify.py`**

```python
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.telegram.client import admin_chat_ids

try:
    from app.agents.booking_graph.context import format_vi_datetime
except ImportError:  # chưa làm Plan 2
    from app.core.vi_format import format_vi_datetime


class TelegramNotifier:
    """Kênh Telegram của chỗ tỏa tin. Chỉ gửi, không nhận — phần nhận là vòng
    lặp polling ở task 4."""

    def __init__(self, client):
        self.client = client

    async def _broadcast(self, text: str) -> None:
        for chat_id in admin_chat_ids():
            await self.client.send_message(chat_id, text)

    async def appointment_created(self, appointment: Appointment) -> None:
        note = f" — {appointment.note}" if appointment.note else ""
        await self._broadcast(
            "Có lịch mới ạ:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}{note}\n"
            f"{appointment.phone or ''}"
        )

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self._broadcast(
            "Khách vừa hủy lịch:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}"
        )

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        if status.is_busy:
            await self._broadcast(
                f"Đã chuyển sang Đang bận, xong lúc {format_vi_datetime(status.busy_until)}."
            )
        else:
            await self._broadcast("Đã chuyển sang Đang rảnh.")
```

- [ ] **Step 4: Viết `app/services/socket_notifier.py`**

```python
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView


class SocketNotifier:
    """Kênh Socket.IO của chỗ tỏa tin.

    `shop_status_changed` phải broadcast tới MỌI user đang online, không chỉ admin:
    thẻ trạng thái trên màn hình chat của khách phải đổi mà không cần tải lại.
    """

    def __init__(self, socketio_service):
        self.socketio = socketio_service

    async def appointment_created(self, appointment: Appointment) -> None:
        await self.socketio.emit_to_admins("appointment_created", {
            "id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
            "user_name": appointment.user_name,
            "phone": appointment.phone,
            "note": appointment.note,
        })

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self.socketio.emit_to_admins("appointment_cancelled", {
            "id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
        })

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        await self.socketio.broadcast("shop_status_changed", {
            "is_busy": status.is_busy,
            "busy_until": status.busy_until.isoformat() if status.busy_until else None,
            "minutes_left": status.minutes_left,
        })
```

- [ ] **Step 5: Phát thông báo từ `AppointmentService`**

Trong `app/services/appointment.py`, thêm import:

```python
from app.services.notifications import notifications
```

Trong `create`, thay `return await self.repo.insert_booked(...)` bằng:

```python
        appointment = await self.repo.insert_booked(
            user_id=user_id,
            user_name=user.full_name,
            phone=user.phone,
            start_at=start,
            duration_minutes=settings.booking_slot_minutes,
            note=note,
            created_via=created_via,
        )
        await notifications.appointment_created(appointment)
        return appointment
```

Trong `cancel`, thay `await self.repo.cancel(appointment_id)` bằng:

```python
        await self.repo.cancel(appointment_id)
        await notifications.appointment_cancelled(appt)
```

- [ ] **Step 6: Phát thông báo khi đổi trạng thái từ web app**

Trong `app/api/v1/routers/shop.py`, thêm import:

```python
from app.services.notifications import notifications
```

Sửa hai endpoint để tỏa tin sau khi đổi:

```python
@router.post("/busy", response_model=ShopStatusResponse)
async def set_busy(
    payload: SetBusyRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    status = await ShopService(db).set_busy(payload.minutes)
    await notifications.shop_status_changed(status)
    return ShopStatusResponse(**status.model_dump())


@router.post("/free", response_model=ShopStatusResponse)
async def set_free(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    status = await ShopService(db).set_free()
    await notifications.shop_status_changed(status)
    return ShopStatusResponse(**status.model_dump())
```

Đây là chỗ dễ sai nhất của cả plan: đổi trạng thái từ **hai** đường (web app và Telegram) mà chỉ một đường tỏa tin thì thẻ trạng thái của khách sẽ hiển thị sai.

- [ ] **Step 7: Đăng ký kênh lúc khởi động**

Trong `main.py`, sau khi tạo `socketio_service` và trước `start_bot(db)`:

```python
    from app.services.notifications import notifications
    from app.services.socket_notifier import SocketNotifier
    from app.telegram.client import TelegramClient, is_configured
    from app.telegram.notify import TelegramNotifier

    notifications.clear()
    notifications.register(SocketNotifier(socketio_service))
    if is_configured():
        notifications.register(TelegramNotifier(TelegramClient()))
```

- [ ] **Step 8: Chạy test để xác nhận pass**

Run: `pytest tests/test_notification_wiring.py -v`
Expected: PASS (5 passed) — quan trọng nhất là `test_telegram_failure_does_not_break_booking`

- [ ] **Step 9: Chạy toàn bộ test**

Run: `pytest -v`
Expected: PASS toàn bộ

- [ ] **Step 10: Kiểm tay đầu-cuối**

Tạo bot qua @BotFather, lấy chat_id theo hướng dẫn trong README, điền `.env`, rồi:

```bash
docker compose up -d mongo postgres
uvicorn main:app --reload
```

Trong Telegram: nhắn `/start` → phải hiện bàn phím 4 nút. Bấm "Tôi đang bận" → "30 phút" → gọi `GET /api/v1/shop/status` phải thấy `is_busy=true`. Đặt lịch qua API bằng tài khoản khách → Telegram phải nhận tin báo kèm tên và SĐT. Nhắn bot từ tài khoản Telegram khác → **không được** có phản hồi nào.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: notify owner over Telegram and Socket.IO on booking changes"
```
