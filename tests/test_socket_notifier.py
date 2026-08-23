from datetime import datetime, timezone

from bson import ObjectId

from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.notifications import Notifier
from app.services.socket_notifier import SocketNotifier


class SpySocketIO:
    """Giả SocketIOService — chỉ ghi lại event và payload đã phát."""

    def __init__(self):
        self.admin_emits = []
        self.broadcasts = []

    async def emit_to_admins(self, event, data):
        self.admin_emits.append((event, data))

    async def broadcast(self, event, data):
        self.broadcasts.append((event, data))


APPT_ID = "68a1f0000000000000000001"


def an_appointment(**overrides):
    fields = dict(
        id=ObjectId(APPT_ID),
        user_id="u1",
        user_name="Cô Lan",
        phone="0912345678",
        start_at=datetime(2026, 8, 7, 8, 30, tzinfo=timezone.utc),
        duration_minutes=60,
        note="làm tóc",
    )
    fields.update(overrides)
    return Appointment(**fields)


async def test_created_goes_to_admins_with_full_payload():
    spy = SpySocketIO()

    await SocketNotifier(spy).appointment_created(an_appointment())

    assert spy.broadcasts == []
    assert spy.admin_emits == [
        (
            "appointment_created",
            {
                "id": APPT_ID,
                "start_at": "2026-08-07T08:30:00+00:00",
                "user_name": "Cô Lan",
                "phone": "0912345678",
                "note": "làm tóc",
            },
        )
    ]


async def test_cancelled_goes_to_admins_with_minimal_payload():
    spy = SpySocketIO()

    await SocketNotifier(spy).appointment_cancelled(an_appointment())

    assert spy.broadcasts == []
    assert spy.admin_emits == [
        (
            "appointment_cancelled",
            {
                "id": APPT_ID,
                "start_at": "2026-08-07T08:30:00+00:00",
            },
        )
    ]


async def test_optional_appointment_fields_stay_in_payload():
    """Lịch không có note/tên/SĐT vẫn phát đủ khoá để client không vỡ."""
    spy = SpySocketIO()

    await SocketNotifier(spy).appointment_created(
        an_appointment(user_name=None, phone=None, note=None)
    )

    _, data = spy.admin_emits[0]
    assert data["user_name"] is None
    assert data["phone"] is None
    assert data["note"] is None


async def test_busy_status_broadcasts_to_everyone():
    """shop_status_changed phải broadcast, không chỉ phát cho admin — thẻ
    trạng thái trên màn hình chat của khách phải đổi mà không cần tải lại."""
    spy = SpySocketIO()
    status = ShopStatusView(
        is_busy=True,
        busy_until=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
        minutes_left=30,
    )

    await SocketNotifier(spy).shop_status_changed(status)

    assert spy.admin_emits == []
    assert spy.broadcasts == [
        (
            "shop_status_changed",
            {
                "is_busy": True,
                "busy_until": "2026-08-07T09:00:00+00:00",
                "minutes_left": 30,
            },
        )
    ]


async def test_free_status_broadcasts_nulls():
    spy = SpySocketIO()

    await SocketNotifier(spy).shop_status_changed(ShopStatusView(is_busy=False))

    assert spy.broadcasts == [
        (
            "shop_status_changed",
            {"is_busy": False, "busy_until": None, "minutes_left": None},
        )
    ]


def test_socket_notifier_implements_notifier_protocol():
    assert isinstance(SocketNotifier(SpySocketIO()), Notifier)
