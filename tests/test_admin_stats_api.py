from datetime import datetime, timezone

import pytest

from app.core.clock import TZ
from app.repositories.appointment import AppointmentRepository
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio

URL = "/api/v1/admin/stats/customers-by-month"


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


async def test_without_token_is_rejected(async_client):
    # FastAPI's shared HTTPBearer() trả 403 "Not authenticated" khi thiếu hẳn
    # header, không phải 401 — đây là quy ước sẵn có toàn app (xem
    # test_chat_history_requires_login trong test_api_flow.py), không phải
    # thứ route này tự quyết định riêng.
    resp = await async_client.get(URL)
    assert resp.status_code == 403


async def test_customer_cannot_read_admin_stats(async_client, user_headers):
    resp = await async_client.get(URL, headers=user_headers)
    assert resp.status_code == 403


async def test_admin_gets_twelve_months_even_with_empty_database(async_client, admin_headers):
    resp = await async_client.get(URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    months = resp.json()["months"]
    assert len(months) == 12
    assert all(m["customer_count"] == 0 for m in months)


async def test_response_months_are_chronological_with_current_month_last(
    async_client, admin_headers
):
    months = (await async_client.get(URL, headers=admin_headers)).json()["months"]
    keys = [m["month"] for m in months]
    assert keys == sorted(keys)
    assert keys[-1] == datetime.now(TZ).strftime("%Y-%m")
    assert all(len(k) == 7 and k[4] == "-" for k in keys)


async def test_counts_distinct_customers_of_current_month(
    async_client, admin_headers, test_db
):
    repo = AppointmentRepository(test_db)
    local = datetime.now(TZ).replace(day=6, hour=8, minute=0, second=0, microsecond=0)
    for user_id, hour in (("u1", 8), ("u1", 9), ("u2", 10)):
        await repo.insert_booked(
            user_id=user_id, user_name="Khách", phone="0912345678",
            start_at=local.replace(hour=hour).astimezone(timezone.utc),
            duration_minutes=60, note=None, created_via="chat",
        )

    months = (await async_client.get(URL, headers=admin_headers)).json()["months"]
    assert months[-1]["customer_count"] == 2
