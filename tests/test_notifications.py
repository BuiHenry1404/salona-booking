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
