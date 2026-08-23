"""Nối thông báo vào nghiệp vụ (Plan 3, task 5).

Hai phần: AppointmentService (tạo/hủy lịch) và router shop (/busy, /free).
Chỉ thao tác THÀNH CÔNG mới tỏa tin; các đường đi ngược hoặc chỉ đọc phải im lặng.
"""

from datetime import datetime

import pytest

from app.core.clock import TZ
from app.core.errors import NotFoundError, SlotTakenError
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.notifications import notifications

pytestmark = pytest.mark.asyncio


class SpyNotifier:
    def __init__(self):
        self.events = []

    async def appointment_created(self, appointment):
        self.events.append(("created", appointment.id))

    async def appointment_cancelled(self, appointment):
        self.events.append(("cancelled", appointment.id))

    async def shop_status_changed(self, status):
        self.events.append(("status", status.is_busy))


class BrokenNotifier:
    async def appointment_created(self, appointment):
        raise RuntimeError("kênh hỏng")

    async def appointment_cancelled(self, appointment):
        raise RuntimeError("kênh hỏng")

    async def shop_status_changed(self, status):
        raise RuntimeError("kênh hỏng")


@pytest.fixture(autouse=True)
def clean_channels():
    notifications.clear()
    yield
    notifications.clear()


@pytest.fixture
def spy():
    channel = SpyNotifier()
    notifications.register(channel)
    return channel


def make_user(phone="0912345678", name="Cô Lan", role="user"):
    return User(phone=phone, hashed_password="h", full_name=name, role=role)


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


async def test_create_fires_appointment_created(test_db, spy):
    appt = await AppointmentService(test_db).create(
        make_user(), future_local(15), note="làm tóc"
    )

    assert spy.events == [("created", appt.id)]


async def test_cancel_fires_appointment_cancelled(test_db, spy):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    spy.events.clear()

    await svc.cancel(user, str(appt.id))

    assert spy.events == [("cancelled", appt.id)]


async def test_idempotent_create_does_not_fire_again(test_db, spy):
    """Nhánh idempotency trả về lịch đã có — không phải một lịch mới, nên
    không được báo tin lần nữa."""
    svc = AppointmentService(test_db)
    user = make_user()
    first = await svc.create(user, future_local(15), note=None)
    second = await svc.create(user, future_local(15), note=None)

    assert first.id == second.id
    assert spy.events == [("created", first.id)]


async def test_slot_taken_fires_no_notification(test_db, spy):
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(15), note=None)

    other = make_user(phone="0938111222", name="Cô Hoa")
    with pytest.raises(SlotTakenError):
        await svc.create(other, future_local(15), note=None)

    assert len(spy.events) == 1  # chỉ lịch thành công đầu tiên


async def test_cancel_unknown_appointment_fires_nothing(test_db, spy):
    with pytest.raises(NotFoundError):
        await AppointmentService(test_db).cancel(
            make_user(), "64b7f0c2e4b0a1a2b3c4d5e6"
        )

    assert spy.events == []


async def test_cancelling_twice_fires_only_once(test_db, spy):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    spy.events.clear()

    await svc.cancel(user, str(appt.id))
    with pytest.raises(NotFoundError):
        await svc.cancel(user, str(appt.id))

    assert spy.events == [("cancelled", appt.id)]


async def test_broken_notifier_does_not_break_booking(test_db):
    """NotificationService nuốt lỗi kênh — đặt lịch vẫn phải thành công."""
    notifications.register(BrokenNotifier())

    appt = await AppointmentService(test_db).create(
        make_user(), future_local(15), note=None
    )
    assert appt.status == "booked"


async def test_broken_notifier_does_not_break_cancelling(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    notifications.clear()
    notifications.register(BrokenNotifier())

    await svc.cancel(user, str(appt.id))
    assert await svc.upcoming_for(user) == []


# ---------------------------------------------------------------------------
# Router shop: POST /busy và POST /free tỏa tin sau khi đổi trạng thái thành công
# ---------------------------------------------------------------------------


async def _login(client, phone, password):
    resp = await client.post(
        "/api/v1/auth/login", json={"phone": phone, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(async_client, test_db):
    await AuthService(test_db).create_user(
        "0901234567", "chutiem123", "Chủ tiệm", role="admin"
    )
    return await _login(async_client, "0901234567", "chutiem123")


@pytest.fixture
async def user_headers(async_client, test_db):
    await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    return await _login(async_client, "0912345678", "matkhau123")


async def test_set_busy_fires_status_changed(async_client, admin_headers, spy):
    resp = await async_client.post(
        "/api/v1/shop/busy", json={"minutes": 30}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_busy"] is True

    assert spy.events == [("status", True)]


async def test_set_free_fires_status_changed(async_client, admin_headers, spy):
    await async_client.post(
        "/api/v1/shop/busy", json={"minutes": 30}, headers=admin_headers
    )
    spy.events.clear()

    resp = await async_client.post("/api/v1/shop/free", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_busy"] is False

    assert spy.events == [("status", False)]


async def test_reading_status_fires_nothing(async_client, user_headers, spy):
    """GET /status là đường chỉ đọc — không được tỏa tin."""
    resp = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert resp.status_code == 200

    assert spy.events == []


async def test_forbidden_busy_call_fires_nothing(async_client, user_headers, spy):
    """Khách thường bị 403 trước khi chạm trạng thái — không có tin nào."""
    resp = await async_client.post(
        "/api/v1/shop/busy", json={"minutes": 30}, headers=user_headers
    )
    assert resp.status_code == 403

    assert spy.events == []


async def test_broken_notifier_does_not_break_set_busy(async_client, admin_headers):
    notifications.register(BrokenNotifier())

    resp = await async_client.post(
        "/api/v1/shop/busy", json={"minutes": 30}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_busy"] is True


async def test_broken_notifier_does_not_break_set_free(async_client, admin_headers):
    notifications.register(BrokenNotifier())

    resp = await async_client.post("/api/v1/shop/free", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_busy"] is False
