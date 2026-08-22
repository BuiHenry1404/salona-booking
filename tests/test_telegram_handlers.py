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
