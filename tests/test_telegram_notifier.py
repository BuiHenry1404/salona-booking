"""TelegramNotifier — kênh Telegram của chỗ tỏa tin (Plan 3, task 5).

Telegram lỗi không được làm hỏng việc đặt lịch: được chốt bằng test
`test_telegram_failure_does_not_break_booking` và bằng chính NotificationService
nuốt lỗi từng kênh (test ở test_notifications.py).
"""

from datetime import datetime

import pytest

from app.core.clock import TZ
from app.models.shop import ShopStatusView
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.notifications import notifications
from app.telegram.notify import TelegramNotifier

pytestmark = pytest.mark.asyncio

ADMIN_ID = 111


class SpyClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text})


class BrokenClient:
    async def send_message(self, *a, **kw):
        raise RuntimeError("mạng chết")


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


def make_user(phone="0912345678", name="Cô Lan", role="user"):
    return User(phone=phone, hashed_password="h", full_name=name, role=role)


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


async def test_booking_notifies_the_owner(test_db, telegram):
    await AppointmentService(test_db).create(
        make_user(), future_local(15), note="làm tóc"
    )

    assert len(telegram.sent) == 1
    text = telegram.sent[0]["text"]
    assert telegram.sent[0]["chat_id"] == ADMIN_ID
    assert "Cô Lan" in text and "làm tóc" in text and "0912345678" in text


async def test_cancelling_notifies_the_owner(test_db, telegram):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note="làm tóc")
    telegram.sent.clear()

    await svc.cancel(user, str(appt.id))
    assert "hủy" in telegram.sent[0]["text"].lower()


async def test_telegram_failure_does_not_break_booking(test_db, monkeypatch):
    """Đặt lịch phải thành công kể cả khi Telegram chết."""
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: {ADMIN_ID})
    notifications.register(TelegramNotifier(BrokenClient()))

    appt = await AppointmentService(test_db).create(
        make_user(), future_local(15), note=None
    )
    assert appt.status == "booked"


async def test_no_admin_chat_id_means_no_message(test_db, monkeypatch):
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: set())
    client = SpyClient()
    notifications.register(TelegramNotifier(client))

    await AppointmentService(test_db).create(make_user(), future_local(15), note=None)
    assert client.sent == []


async def test_status_change_notifies_the_owner(telegram):
    await notifications.shop_status_changed(
        ShopStatusView(is_busy=True, minutes_left=30)
    )
    assert "bận" in telegram.sent[0]["text"].lower()
