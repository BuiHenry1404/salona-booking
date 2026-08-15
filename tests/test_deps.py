import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_admin
from app.core.security import create_access_token_for
from app.models.user import User
from app.services.auth import AuthService


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    def me(user: User = Depends(get_current_user)):
        return {"phone": user.phone}

    @app.get("/admin")
    def admin(user: User = Depends(require_admin)):
        return {"ok": True}

    return app


def test_admin_route_rejects_a_normal_user():
    app = _app()
    normal = User(phone="0912345678", hashed_password="h", full_name="A", role="user")
    app.dependency_overrides[get_current_user] = lambda: normal

    with TestClient(app) as client:
        assert client.get("/admin").status_code == 403


def test_admin_route_accepts_an_admin():
    app = _app()
    boss = User(phone="0901234567", hashed_password="h", full_name="Chủ tiệm", role="admin")
    app.dependency_overrides[get_current_user] = lambda: boss

    with TestClient(app) as client:
        assert client.get("/admin").status_code == 200


def test_me_returns_the_authenticated_user():
    app = _app()
    normal = User(phone="0912345678", hashed_password="h", full_name="Cô Lan", role="user")
    app.dependency_overrides[get_current_user] = lambda: normal

    with TestClient(app) as client:
        assert client.get("/me").json() == {"phone": "0912345678"}


async def test_a_token_issued_before_the_password_changed_is_refused(test_db):
    """Chiếm tài khoản rồi nạn nhân đổi lại mật khẩu — token của kẻ chiếm phải chết ngay,
    không sống tiếp tới hết JWT_EXPIRE_MINUTES."""
    svc = AuthService(test_db)
    user = await svc.create_user("0912345678", "matkhau123", "Cô Lan")
    token = create_access_token_for(user)

    await svc.reset_password("0912345678", "matkhaumoi456", ip="1.2.3.4")

    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), test_db)
    assert exc.value.status_code == 401


async def test_a_token_issued_after_the_password_changed_still_works(test_db):
    svc = AuthService(test_db)
    await svc.create_user("0912345678", "matkhau123", "Cô Lan")
    await svc.reset_password("0912345678", "matkhaumoi456", ip="1.2.3.4")

    fresh = await svc.authenticate("0912345678", "matkhaumoi456", ip="1.2.3.4")
    token = create_access_token_for(fresh)
    assert (await get_current_user(_creds(token), test_db)).phone == "0912345678"


async def test_changing_one_password_does_not_log_everyone_else_out(test_db):
    svc = AuthService(test_db)
    other = await svc.create_user("0987654321", "matkhau123", "Cô Hoa")
    await svc.create_user("0912345678", "matkhau123", "Cô Lan")
    token = create_access_token_for(other)

    await svc.reset_password("0912345678", "matkhaumoi456", ip="1.2.3.4")

    assert (await get_current_user(_creds(token), test_db)).phone == "0987654321"


async def test_an_account_that_never_changed_its_password_keeps_working(test_db):
    svc = AuthService(test_db)
    user = await svc.create_user("0912345678", "matkhau123", "Cô Lan")
    await test_db["users"].update_one({"_id": user.id}, {"$unset": {"token_version": ""}})
    token = create_access_token_for(user)
    assert (await get_current_user(_creds(token), test_db)).phone == "0912345678"
