# Task 10 · Schema API và dependency phân quyền

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Rewrite: `app/api/v1/schemas.py`
- Modify: `app/api/deps.py`
- Create: `tests/test_deps.py`

**Interfaces:**
- Consumes: `AuthService` (Task 6), `User` (Task 4)
- Produces:
  - Schema: `LoginRequest(phone, password)`, `TokenResponse(access_token, token_type, role)`, `CreateUserRequest(phone, password, full_name, role)`, `ResetPasswordRequest(phone, new_password)`, `UserResponse(id, phone, full_name, role, is_active)`, `AppointmentCreateRequest(start_at, note)`, `AppointmentResponse(id, start_at, duration_minutes, note, status, user_name, phone)`, `ShopStatusResponse(is_busy, busy_until, minutes_left)`, `SetBusyRequest(minutes)`, `ShopHoursRequest(open_time, close_time, closed_days)`, `FreeSlotsResponse(slots)`
  - Dependency: `get_current_user(...) -> User`, `require_admin(...) -> User`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_deps.py`:

```python
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
```

`require_admin` phụ thuộc `get_current_user`, nên chỉ cần ghi đè `get_current_user` là FastAPI tự dùng user giả cho cả hai. Không cần DB, không cần token thật.

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_deps.py -v`
Expected: FAIL với `ImportError: cannot import name 'require_admin'`

- [ ] **Step 3: Viết lại `app/api/v1/schemas.py`**

```python
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    phone: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Literal["user", "admin"]


class CreateUserRequest(BaseModel):
    phone: str
    password: str = Field(..., min_length=4)
    full_name: Optional[str] = None
    role: Literal["user", "admin"] = "user"


class ResetPasswordRequest(BaseModel):
    phone: str
    new_password: str = Field(..., min_length=4)


class UserResponse(BaseModel):
    id: str
    phone: str
    full_name: Optional[str] = None
    role: Literal["user", "admin"]
    is_active: bool


class AppointmentCreateRequest(BaseModel):
    start_at: datetime
    note: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: str
    start_at: datetime
    duration_minutes: int
    note: Optional[str] = None
    status: Literal["booked", "cancelled"]
    user_name: Optional[str] = None
    phone: Optional[str] = None


class ShopStatusResponse(BaseModel):
    is_busy: bool
    busy_until: Optional[datetime] = None
    minutes_left: Optional[int] = None


class SetBusyRequest(BaseModel):
    minutes: int = Field(..., ge=5, le=480)


class ShopHoursRequest(BaseModel):
    open_time: str = "08:00"
    close_time: str = "19:00"
    closed_days: List[int] = Field(default_factory=list)


class FreeSlotsResponse(BaseModel):
    slots: List[datetime]
```

- [ ] **Step 4: Thêm dependency phân quyền vào `app/api/deps.py`**

Thêm vào cuối file (giữ nguyên `get_db` sẵn có):

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import verify_token
from app.models.user import User
from app.services.auth import AuthService

_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> User:
    try:
        payload = verify_token(credentials.credentials)
        user_id = payload.get("sub")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã hết hạn")

    user = await AuthService(db).get_user_by_id(user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã hết hạn")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Chặn ở backend, không chỉ ẩn trên React."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền")
    return user
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_deps.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/schemas.py app/api/deps.py tests/test_deps.py
git commit -m "feat: phone-based API schemas and admin authorisation dependency"
```

---
