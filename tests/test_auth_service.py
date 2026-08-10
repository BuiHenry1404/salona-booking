import pytest

from app.core.errors import AppError, NotFoundError, RateLimitedError
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


async def _seed(db, phone="0912345678", password="matkhau123"):
    svc = AuthService(db)
    await svc.create_user(phone=phone, password=password, full_name="Cô Lan")
    return svc


async def test_login_with_correct_password(test_db):
    svc = await _seed(test_db)
    user = await svc.authenticate("0912345678", "matkhau123")
    assert user is not None and user.full_name == "Cô Lan"


async def test_login_accepts_any_phone_format(test_db):
    svc = await _seed(test_db)
    assert await svc.authenticate("+84 912 345 678", "matkhau123") is not None


async def test_login_with_wrong_password_returns_none(test_db):
    svc = await _seed(test_db)
    assert await svc.authenticate("0912345678", "sai") is None


async def test_login_with_unknown_phone_returns_none(test_db):
    svc = await _seed(test_db)
    assert await svc.authenticate("0900000000", "matkhau123") is None


async def test_inactive_user_cannot_log_in(test_db):
    svc = await _seed(test_db)
    await test_db["users"].update_one({"phone": "0912345678"}, {"$set": {"is_active": False}})
    assert await svc.authenticate("0912345678", "matkhau123") is None


async def test_duplicate_phone_is_rejected(test_db):
    svc = await _seed(test_db)
    with pytest.raises(AppError):
        await svc.create_user(phone="0912345678", password="x", full_name="Người khác")


async def test_reset_password_changes_the_password(test_db):
    svc = await _seed(test_db)
    await svc.reset_password("0912345678", "matkhaumoi456", ip="1.2.3.4")
    assert await svc.authenticate("0912345678", "matkhaumoi456") is not None
    assert await svc.authenticate("0912345678", "matkhau123") is None


async def test_reset_password_for_unknown_phone_raises(test_db):
    svc = await _seed(test_db)
    with pytest.raises(NotFoundError):
        await svc.reset_password("0900000000", "x", ip="1.2.3.4")


async def test_reset_password_is_rate_limited_per_phone(test_db):
    svc = await _seed(test_db)
    for i in range(5):
        await svc.reset_password("0912345678", f"matkhau{i}xyz", ip=f"9.9.9.{i}")
    with pytest.raises(RateLimitedError):
        await svc.reset_password("0912345678", "lai_nua", ip="9.9.9.99")


async def test_reset_password_is_rate_limited_per_ip(test_db):
    svc = AuthService(test_db)
    for i in range(5):
        await svc.create_user(phone=f"09123456{i:02d}", password="x", full_name=None)
        await svc.reset_password(f"09123456{i:02d}", "matkhaumoi123", ip="7.7.7.7")
    await svc.create_user(phone="0999999999", password="x", full_name=None)
    with pytest.raises(RateLimitedError):
        await svc.reset_password("0999999999", "matkhaumoi123", ip="7.7.7.7")
