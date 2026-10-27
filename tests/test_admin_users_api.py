"""PATCH /admin/users/{id}/note — ghi chú riêng của tiệm (Task 13)."""
import pytest

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


async def test_admin_can_set_staff_note(async_client, test_db, admin_headers):
    user = await AuthService(test_db).create_user("0987654321", "matkhau123", "Chị Hoa")

    resp = await async_client.patch(
        f"/api/v1/admin/users/{user.id}/note",
        json={"staff_note": "hay đến trễ 15 phút"},
        headers=admin_headers,
    )

    assert resp.status_code == 204, resp.text
    from app.repositories.user import UserRepository
    updated = await UserRepository(test_db).get_by_id(str(user.id))
    assert updated.staff_note == "hay đến trễ 15 phút"


async def test_note_longer_than_200_is_rejected(async_client, test_db, admin_headers):
    user = await AuthService(test_db).create_user("0987654321", "matkhau123", "Chị Hoa")

    resp = await async_client.patch(
        f"/api/v1/admin/users/{user.id}/note",
        json={"staff_note": "x" * 201},
        headers=admin_headers,
    )

    assert resp.status_code == 422


async def test_non_admin_cannot_set_staff_note(async_client, test_db, user_headers):
    other = await AuthService(test_db).create_user("0987654321", "matkhau123", "Chị Hoa")

    resp = await async_client.patch(
        f"/api/v1/admin/users/{other.id}/note",
        json={"staff_note": "ghi chú"},
        headers=user_headers,
    )

    assert resp.status_code == 403


async def test_unknown_user_id_is_404(async_client, admin_headers):
    resp = await async_client.patch(
        "/api/v1/admin/users/000000000000000000000000/note",
        json={"staff_note": "ghi chú"},
        headers=admin_headers,
    )

    assert resp.status_code == 404
