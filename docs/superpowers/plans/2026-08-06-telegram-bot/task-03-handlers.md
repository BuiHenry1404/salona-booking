# Task 3 · Xử lý nút bấm và ủy quyền

> Thuộc plan [Bot Telegram](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/telegram/handlers.py`, `tests/test_telegram_handlers.py`

**Interfaces:**
- Consumes: `TelegramClient`, `MAIN_KEYBOARD`, `duration_keyboard`, `admin_chat_ids` (task 2); `ShopService`, `AppointmentService` (Plan 1); `format_vi_datetime` (Plan 2 task 4 — nếu chưa có Plan 2, sao chép hàm vào `app/core/vi_format.py`)
- Produces: `TelegramHandlers(db, client).handle_update(update: dict) -> None`

- [ ] **Step 1: Đảm bảo có hàm định dạng giờ tiếng Việt**

Nếu `app/agents/booking_graph/context.py` **chưa tồn tại** (chưa làm Plan 2), tạo `app/core/vi_format.py`:

```python
from app.core.clock import to_local

_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def format_vi_datetime(dt) -> str:
    """'Thứ Sáu 7/8, 3:00 chiều' — cách người Việt lớn tuổi thực sự nói giờ."""
    local = to_local(dt)
    hour = local.hour
    if hour < 12:
        period, display = "sáng", hour
    elif hour < 18:
        period, display = "chiều", hour - 12 if hour > 12 else 12
    else:
        period, display = "tối", hour - 12
    return (f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, "
            f"{display}:{local.minute:02d} {period}")
```

Nếu đã có Plan 2, import từ `app.agents.booking_graph.context` thay vì tạo file mới, và bỏ qua bước này.

- [ ] **Step 2: Viết test (sẽ fail)**

Tạo `tests/test_telegram_handlers.py`:

```python
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ
from app.services.auth import AuthService
from app.services.shop import ShopService
from app.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio

ADMIN_ID = 111
STRANGER_ID = 999


class SpyClient:
    def __init__(self):
        self.sent = []
        self.answered = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    async def answer_callback(self, callback_id, text=""):
        self.answered.append((callback_id, text))


@pytest.fixture
def handlers(test_db, monkeypatch):
    monkeypatch.setattr(
        "app.telegram.handlers.admin_chat_ids", lambda: {ADMIN_ID}
    )
    client = SpyClient()
    return TelegramHandlers(test_db, client), client


def message(text, chat_id=ADMIN_ID):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def callback(data, chat_id=ADMIN_ID):
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "data": data, "message": {"chat": {"id": chat_id}}}}


async def test_stranger_gets_absolutely_no_reply(handlers):
    """Bỏ qua im lặng: người lạ dò ra bot cũng không biết nó làm gì."""
    h, client = handlers
    await h.handle_update(message("Hôm nay", chat_id=STRANGER_ID))
    assert client.sent == []
    assert client.answered == []


async def test_stranger_callback_is_ignored_too(handlers):
    h, client = handlers
    await h.handle_update(callback("busy:30", chat_id=STRANGER_ID))
    assert client.sent == [] and client.answered == []


async def test_start_shows_the_keyboard(handlers):
    h, client = handlers
    await h.handle_update(message("/start"))
    assert client.sent[0]["keyboard"] is not None


async def test_busy_button_offers_four_durations(handlers):
    h, client = handlers
    await h.handle_update(message("Tôi đang bận"))
    buttons = client.sent[-1]["keyboard"]["inline_keyboard"]
    assert [b["callback_data"] for row in buttons for b in row] == [
        "busy:15", "busy:30", "busy:60", "busy:120"
    ]


async def test_choosing_a_duration_sets_the_status(handlers, test_db):
    h, client = handlers
    await h.handle_update(callback("busy:30"))

    status = await ShopService(test_db).get_status()
    assert status.is_busy is True
    assert 28 <= status.minutes_left <= 30
    assert client.answered  # phải trả lời callback, nếu không Telegram hiện đồng hồ quay mãi


async def test_callback_is_answered_before_the_slow_work(handlers, test_db, monkeypatch):
    """Telegram cho callback query hạn 10 giây. Tỏa tin đi Socket.IO + Telegram
    mất vài vòng HTTP, để sau thì nút quay mãi và chủ tiệm bấm lại lần nữa."""
    h, client = handlers
    order = []

    async def slow_broadcast(status):
        order.append("broadcast")

    monkeypatch.setattr(
        "app.telegram.handlers.notifications.shop_status_changed", slow_broadcast
    )
    original = client.answer_callback

    async def spy(callback_id, text=""):
        order.append("answered")
        await original(callback_id, text)

    client.answer_callback = spy

    await h.handle_update(callback("busy:30"))
    assert order[0] == "answered"


async def test_free_button_clears_the_status(handlers, test_db):
    h, client = handlers
    await ShopService(test_db).set_busy(60)

    await h.handle_update(message("Tôi rảnh rồi"))
    assert (await ShopService(test_db).get_status()).is_busy is False


async def test_today_with_no_appointments(handlers):
    h, client = handlers
    await h.handle_update(message("Hôm nay"))
    assert "không có lịch" in client.sent[-1]["text"].lower()


async def test_today_lists_the_appointments(handlers, test_db):
    h, client = handlers
    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")

    from app.services.appointment import AppointmentService
    today_at_15 = datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0)
    if today_at_15 < datetime.now(TZ):
        pytest.skip("đã qua 3 giờ chiều, không đặt được lịch hôm nay")
    await AppointmentService(test_db).create(user, today_at_15, note="làm tóc")

    await h.handle_update(message("Hôm nay"))
    text = client.sent[-1]["text"]
    assert "Cô Lan" in text and "làm tóc" in text and "0912345678" in text


async def test_tomorrow_looks_at_the_next_day(handlers, test_db):
    h, client = handlers
    user = await AuthService(test_db).create_user("0912345678", "x", "Cô Lan")

    from app.services.appointment import AppointmentService
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    await AppointmentService(test_db).create(user, tomorrow, note="làm nail")

    await h.handle_update(message("Ngày mai"))
    assert "làm nail" in client.sent[-1]["text"]


async def test_unknown_text_gets_a_gentle_nudge_not_silence(handlers):
    h, client = handlers
    await h.handle_update(message("alo alo"))
    assert client.sent  # admin xứng đáng có phản hồi, khác với người lạ


async def test_malformed_update_does_not_crash(handlers):
    h, client = handlers
    await h.handle_update({})
    await h.handle_update({"message": {}})
    assert client.sent == []
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_telegram_handlers.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.telegram.handlers'`

- [ ] **Step 4: Viết `app/telegram/handlers.py`**

```python
from datetime import timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.core.logging import get_logger
from app.services.appointment import AppointmentService
from app.services.notifications import notifications
from app.services.shop import ShopService
from app.telegram.client import (MAIN_KEYBOARD, admin_chat_ids,
                                 duration_keyboard)

try:
    from app.agents.booking_graph.context import format_vi_datetime
except ImportError:  # chưa làm Plan 2
    from app.core.vi_format import format_vi_datetime

logger = get_logger(__name__)

GREETING = (
    "Dạ chào chủ tiệm. Bấm nút bên dưới để xem lịch hoặc đổi trạng thái bận rảnh ạ."
)
NUDGE = "Dạ chủ tiệm bấm một trong bốn nút bên dưới giúp con ạ."


class TelegramHandlers:
    """Xử lý update từ Telegram. Hoàn toàn tất định — bấm nút nào gọi service nấy,
    không có AI, không tốn token, test không cần LLM."""

    def __init__(self, db: AsyncIOMotorDatabase, client):
        self.db = db
        self.client = client
        self.shop = ShopService(db)
        self.appointments = AppointmentService(db)

    async def handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback(update["callback_query"])
            elif "message" in update:
                await self._handle_message(update["message"])
        except Exception as exc:
            logger.warning("telegram_update_failed", extra={"error": str(exc)})

    @staticmethod
    def _chat_id(payload: dict) -> Optional[int]:
        return ((payload or {}).get("chat") or {}).get("id")

    def _authorised(self, chat_id: Optional[int]) -> bool:
        return chat_id is not None and chat_id in admin_chat_ids()

    async def _handle_message(self, message: dict) -> None:
        chat_id = self._chat_id(message)
        # Bỏ qua IM LẶNG: không trả lời gì để người lạ dò ra bot cũng không biết
        # nó làm gì. Bot đọc được tên và số điện thoại khách.
        if not self._authorised(chat_id):
            return

        text = (message.get("text") or "").strip()

        if text in ("/start", "/help"):
            await self.client.send_message(chat_id, GREETING, keyboard=MAIN_KEYBOARD)
        elif text == "Hôm nay":
            await self._send_schedule(chat_id, days_ahead=0, label="hôm nay")
        elif text == "Ngày mai":
            await self._send_schedule(chat_id, days_ahead=1, label="ngày mai")
        elif text == "Tôi đang bận":
            await self.client.send_message(
                chat_id, "Chủ tiệm bận khoảng bao lâu ạ?", keyboard=duration_keyboard()
            )
        elif text == "Tôi rảnh rồi":
            status = await self.shop.set_free()
            await notifications.shop_status_changed(status)
            await self.client.send_message(
                chat_id, "Đã chuyển sang Đang rảnh ạ.", keyboard=MAIN_KEYBOARD
            )
        else:
            await self.client.send_message(chat_id, NUDGE, keyboard=MAIN_KEYBOARD)

    async def _handle_callback(self, query: dict) -> None:
        chat_id = self._chat_id(query.get("message") or {})
        if not self._authorised(chat_id):
            return

        data = query.get("data") or ""
        if not data.startswith("busy:"):
            await self.client.answer_callback(query["id"])
            return

        # Trả lời TRƯỚC khi làm việc. Telegram cho callback query hạn 10 giây;
        # `shop_status_changed` tỏa tin ra cả Socket.IO lẫn Telegram, mỗi kênh
        # một vòng HTTP — để sau thì nút quay mãi và chủ tiệm bấm lại lần nữa.
        await self.client.answer_callback(query["id"], "Đã ghi nhận")

        minutes = int(data.split(":", 1)[1])
        status = await self.shop.set_busy(minutes)
        await notifications.shop_status_changed(status)

        until = to_local(status.busy_until).strftime("%H:%M")
        await self.client.send_message(
            chat_id,
            f"Đã chuyển sang Đang bận, khoảng {minutes} phút — xong lúc {until} ạ.",
            keyboard=MAIN_KEYBOARD,
        )

    async def _send_schedule(self, chat_id: int, days_ahead: int, label: str) -> None:
        day = (to_local(now_utc()) + timedelta(days=days_ahead)).date()
        appointments = await self.appointments.day_schedule(day)

        if not appointments:
            await self.client.send_message(
                chat_id, f"Dạ {label} không có lịch nào ạ.", keyboard=MAIN_KEYBOARD
            )
            return

        lines = [f"Lịch {label} — {len(appointments)} khách:"]
        for appt in appointments:
            note = f" — {appt.note}" if appt.note else ""
            lines.append(
                f"• {format_vi_datetime(appt.start_at)} · {appt.user_name or 'khách'}"
                f"{note}\n  {appt.phone or ''}"
            )
        await self.client.send_message(chat_id, "\n".join(lines), keyboard=MAIN_KEYBOARD)
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_telegram_handlers.py -v`
Expected: PASS (12 passed) — quan trọng nhất là `test_stranger_gets_absolutely_no_reply`

- [ ] **Step 6: Commit**

```bash
git add app/telegram/handlers.py app/core/vi_format.py tests/test_telegram_handlers.py
git commit -m "feat: Telegram button handlers with silent rejection of strangers"
```
