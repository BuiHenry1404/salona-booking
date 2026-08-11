from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


async def _login(client, phone, password):
    resp = await client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(async_client, test_db):
    await AuthService(test_db).create_user("0901234567", "chutiem123", "Chủ tiệm", role="admin")
    return await _login(async_client, "0901234567", "chutiem123")


@pytest.fixture
async def user_headers(async_client, test_db):
    await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    return await _login(async_client, "0912345678", "matkhau123")


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


async def test_login_with_wrong_password_returns_401(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/auth/login", json={"phone": "0912345678", "password": "sai"}
    )
    assert resp.status_code == 401


async def test_normal_user_cannot_create_accounts(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/auth/users",
        json={"phone": "0999888777", "password": "abcd", "full_name": "X"},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_admin_can_create_accounts(async_client, admin_headers):
    resp = await async_client.post(
        "/api/v1/auth/users",
        json={"phone": "0999888777", "password": "abcd", "full_name": "Khách mới"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["phone"] == "0999888777"


async def test_book_then_list_then_cancel(async_client, user_headers):
    created = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(15), "note": "làm tóc"},
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]

    listed = await async_client.get("/api/v1/appointments/mine", headers=user_headers)
    assert [a["id"] for a in listed.json()] == [appointment_id]

    deleted = await async_client.delete(
        f"/api/v1/appointments/{appointment_id}", headers=user_headers
    )
    assert deleted.status_code == 204

    listed_again = await async_client.get("/api/v1/appointments/mine", headers=user_headers)
    assert listed_again.json() == []


async def test_double_booking_returns_409(async_client, user_headers, admin_headers):
    payload = {"start_at": tomorrow_at(16), "note": None}
    first = await async_client.post("/api/v1/appointments", json=payload, headers=user_headers)
    assert first.status_code == 201

    clash = await async_client.post("/api/v1/appointments", json=payload, headers=admin_headers)
    assert clash.status_code == 409


async def test_booking_at_3am_returns_400(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/appointments", json={"start_at": tomorrow_at(3), "note": None}, headers=user_headers
    )
    assert resp.status_code == 400


async def test_shop_status_flow(async_client, admin_headers, user_headers):
    free = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert free.json()["is_busy"] is False

    await async_client.post("/api/v1/shop/busy", json={"minutes": 30}, headers=admin_headers)
    busy = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert busy.json()["is_busy"] is True
    assert busy.json()["minutes_left"] <= 30

    await async_client.post("/api/v1/shop/free", headers=admin_headers)
    assert (await async_client.get("/api/v1/shop/status", headers=user_headers)).json()["is_busy"] is False


async def test_normal_user_cannot_change_shop_status(async_client, user_headers):
    resp = await async_client.post("/api/v1/shop/busy", json={"minutes": 30}, headers=user_headers)
    assert resp.status_code == 403


async def test_mongo_down_returns_503_with_shop_phone(async_client, user_headers, monkeypatch):
    """Mongo chết là fail-hard: 503 kèm SĐT tiệm để khách gọi người thật.

    Tuyệt đối không để traceback của Motor lọt ra ngoài — người lớn tuổi
    thấy chữ tiếng Anh là nghĩ mình làm hỏng máy.
    """
    from pymongo.errors import ServerSelectionTimeoutError

    from app.core.config import settings
    from app.services.shop import ShopService

    async def _die(self):
        raise ServerSelectionTimeoutError("no primary available")

    monkeypatch.setattr(ShopService, "get_status", _die)
    monkeypatch.setattr(settings, "shop_phone", "0287654321")

    resp = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert "0287654321" in body["detail"]
    assert body["shop_phone"] == "0287654321"
    assert "ServerSelection" not in body["detail"]
