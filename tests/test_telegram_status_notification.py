"""Regression test cho lỗi trùng tin trạng thái từ Telegram.

Khi chủ tiệm bấm "Tôi đang bận" hoặc "Tôi rảnh rồi", chỉ có **một** tin
thông báo trạng thái được gửi qua Telegram — qua kênh TelegramNotifier của
chỗ tỏa tin duy nhất. Handler không được gửi tin thứ hai trực tiếp.
"""

import pytest

from app.services.notifications import notifications
from app.services.shop import ShopService
from app.telegram.client import MAIN_KEYBOARD
from app.telegram.handlers import TelegramHandlers
from app.telegram.notify import TelegramNotifier

pytestmark = pytest.mark.asyncio

ADMIN_ID = 111


class _SpyClient:
    def __init__(self):
        self.sent = []
        self.answered = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    async def answer_callback(self, callback_id, text=""):
        self.answered.append((callback_id, text))


class _SpyNotifier:
    """Một kênh Notifier generic để đếm sự kiện shop_status_changed."""

    def __init__(self):
        self.events = []

    async def appointment_created(self, appointment):
        pass

    async def appointment_cancelled(self, appointment):
        pass

    async def shop_status_changed(self, status):
        self.events.append(status)


def _message(text, chat_id=ADMIN_ID):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def _callback(data, chat_id=ADMIN_ID):
    return {
        "update_id": 2,
        "callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat_id}}},
    }


@pytest.fixture
def handler_client(monkeypatch, test_db):
    monkeypatch.setattr("app.telegram.handlers.admin_chat_ids", lambda: {ADMIN_ID})
    # TelegramNotifier._broadcast gọi `admin_chat_ids` đã import vào
    # app.telegram.notify — vá mỗi bên handlers thì notifier vẫn đọc config thật,
    # mà TELEGRAM_ADMIN_CHAT_IDS trong .env đang rỗng nên nó không gửi cho ai.
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: {ADMIN_ID})
    return _SpyClient()


@pytest.fixture(autouse=True)
def _clean_notifications():
    notifications.clear()
    yield
    notifications.clear()


async def test_busy_callback_triggers_only_one_telegram_status_message(
    test_db, handler_client, monkeypatch
):
    """Bấm 'Tôi đang bận' → '30 phút' chỉ sinh ra đúng 1 tin Telegram thông báo trạng thái."""
    telegram_client = _SpyClient()
    notifications.register(TelegramNotifier(telegram_client))

    handlers = TelegramHandlers(test_db, handler_client)
    await handlers.handle_update(_callback("busy:30"))

    # Callback được trả lời (tránh đồng hồ quay mãi).
    assert len(handler_client.answered) == 1
    # Handler không được tự gửi tin trạng thái thứ hai.
    assert handler_client.sent == []
    # Chỗ tỏa tin gửi qua TelegramNotifier đúng 1 lần.
    assert len(telegram_client.sent) == 1
    assert "bận" in telegram_client.sent[0]["text"].lower()


async def test_free_text_triggers_only_one_telegram_status_message(
    test_db, handler_client, monkeypatch
):
    """Bấm 'Tôi rảnh rồi' chỉ sinh ra đúng 1 tin Telegram thông báo trạng thái."""
    telegram_client = _SpyClient()
    notifications.register(TelegramNotifier(telegram_client))

    handlers = TelegramHandlers(test_db, handler_client)
    await handlers.handle_update(_message("Tôi rảnh rồi"))

    # Handler không được tự gửi tin trạng thái.
    assert handler_client.sent == []
    # Chỗ tỏa tin gửi qua TelegramNotifier đúng 1 lần.
    assert len(telegram_client.sent) == 1
    assert "rảnh" in telegram_client.sent[0]["text"].lower()


async def test_callback_is_answered_before_broadcast_still_holds(
    test_db, handler_client, monkeypatch
):
    """Callback vn phải được trả lời TRƯỚC khi tỏa tin đi."""
    order = []

    async def slow_broadcast(status):
        order.append("broadcast")

    monkeypatch.setattr(
        "app.telegram.handlers.notifications.shop_status_changed", slow_broadcast
    )

    async def spy_answer(callback_id, text=""):
        order.append("answered")

    handler_client.answer_callback = spy_answer

    handlers = TelegramHandlers(test_db, handler_client)
    await handlers.handle_update(_callback("busy:30"))

    assert order[0] == "answered"


async def test_total_status_notifications_for_busy_is_one_with_mixed_channels(
    test_db, handler_client, monkeypatch
):
    """Nếu vừa có TelegramNotifier vừa có kênh khác, busy callback chỉ phát 1 lần shop_status_changed."""
    telegram_client = _SpyClient()
    notifications.register(TelegramNotifier(telegram_client))
    generic = _SpyNotifier()
    notifications.register(generic)

    handlers = TelegramHandlers(test_db, handler_client)
    await handlers.handle_update(_callback("busy:30"))

    # Mỗi kênh nhận đúng 1 sự kiện.
    assert len(generic.events) == 1
    assert len(telegram_client.sent) == 1
    # Handler không gửi tin thêm.
    assert handler_client.sent == []
