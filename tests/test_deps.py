from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_admin
from app.models.user import User


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
