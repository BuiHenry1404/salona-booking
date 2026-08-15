import pytest

from app.core.config import settings
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio

COOKIE = "refresh_token"


async def _seed(db, phone="0912345678", password="matkhau123"):
    await AuthService(db).create_user(phone=phone, password=password, full_name="Cô Lan")


async def _login(client):
    return await client.post(
        "/api/v1/auth/login", json={"phone": "0912345678", "password": "matkhau123"}
    )


def _set_cookie_header(resp) -> str:
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{COOKIE}="):
            return raw
    raise AssertionError(f"không có Set-Cookie cho {COOKIE}: {resp.headers}")


async def test_login_never_puts_the_refresh_token_in_the_body(async_client, test_db):
    """Trả trong body là buộc React cất ở nơi JavaScript đọc được — một lỗ XSS
    là mất sạch phiên của khách."""
    await _seed(test_db)
    resp = await _login(async_client)

    assert resp.status_code == 200
    assert "refresh_token" not in resp.json()
    assert resp.json()["access_token"]


async def test_login_sets_an_httponly_cookie_scoped_to_the_auth_path(async_client, test_db):
    await _seed(test_db)
    header = _set_cookie_header(await _login(async_client)).lower()

    assert "httponly" in header
    assert "samesite=strict" in header
    assert "path=/api/v1/auth" in header


async def test_the_cookie_is_marked_secure_when_configured(async_client, test_db, monkeypatch):
    """Prod chạy HTTPS thì cookie phải có Secure. Dev chạy HTTP nên tắt được."""
    await _seed(test_db)
    monkeypatch.setattr(settings, "cookie_secure", True)

    assert "secure" in _set_cookie_header(await _login(async_client)).lower()


async def test_refresh_works_from_the_cookie_alone(async_client, test_db):
    await _seed(test_db)
    await _login(async_client)

    resp = await async_client.post("/api/v1/auth/refresh")

    assert resp.status_code == 200
    me = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
    )
    assert me.status_code == 200


async def test_refresh_rotates_the_cookie(async_client, test_db):
    await _seed(test_db)
    first = _set_cookie_header(await _login(async_client))

    second = _set_cookie_header(await async_client.post("/api/v1/auth/refresh"))

    assert first != second


async def test_refresh_without_a_cookie_is_401(async_client, test_db):
    await _seed(test_db)
    resp = await async_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


async def test_logout_clears_the_cookie_and_kills_the_session(async_client, test_db):
    await _seed(test_db)
    await _login(async_client)

    assert (await async_client.post("/api/v1/auth/logout")).status_code == 204
    assert await test_db["refresh_tokens"].count_documents({}) == 0
    assert (await async_client.post("/api/v1/auth/refresh")).status_code == 401


async def test_logout_without_a_cookie_is_still_204(async_client, test_db):
    """Nút đăng xuất không bao giờ được báo lỗi cho khách."""
    await _seed(test_db)
    assert (await async_client.post("/api/v1/auth/logout")).status_code == 204


async def test_a_stolen_cookie_replayed_late_kills_the_session(async_client, test_db):
    await _seed(test_db)
    await _login(async_client)
    stolen = async_client.cookies[COOKIE]

    await async_client.post("/api/v1/auth/refresh")
    await test_db["refresh_tokens"].update_many(
        {"used_at": {"$ne": None}},
        [{"$set": {"used_at": {"$subtract": ["$used_at", 3600 * 1000]}}}],
    )

    async_client.cookies.set(COOKIE, stolen)
    assert (await async_client.post("/api/v1/auth/refresh")).status_code == 401
    assert await test_db["refresh_tokens"].count_documents({}) == 0


async def test_a_garbage_cookie_is_401(async_client, test_db):
    await _seed(test_db)
    async_client.cookies.set(COOKIE, "khong-phai-token-that")
    assert (await async_client.post("/api/v1/auth/refresh")).status_code == 401
