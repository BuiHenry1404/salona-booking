"""Tin Telegram khi có lịch mới phải nói tên dịch vụ và giờ xong — khách lớn
tuổi cần mốc giờ (Task 3), không phải chỉ giờ bắt đầu như trước."""

from datetime import datetime

import pytest

from app.core.clock import TZ
from app.models.service import Service
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.notifications import notifications
from app.services.service_catalog import ServiceCatalogService
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
def telegram(monkeypatch, test_db):
    monkeypatch.setattr("app.telegram.notify.admin_chat_ids", lambda: {ADMIN_ID})
    client = SpyClient()
    notifications.register(TelegramNotifier(client, test_db))
    return client


def make_user(phone="0912345678", name="Cô Lan", role="user"):
    return User(phone=phone, hashed_password="h", full_name=name, role=role)


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


async def test_new_appointment_message_has_services_and_end_time(test_db, telegram):
    await ServiceCatalogService(test_db).upsert_many([
        Service(
            id="toc-nhuom", category="toc", name="Nhuộm",
            aliases=["nhuộm tóc"], price_min=350000, price_max=700000,
            duration_minutes=120,
        ),
    ])

    await AppointmentService(test_db).create(
        make_user(), future_local(9), note="làm tóc", service_ids=["toc-nhuom"],
    )

    text = telegram.sent[0]["text"]
    assert "Nhuộm" in text
    assert "xong lúc" in text
