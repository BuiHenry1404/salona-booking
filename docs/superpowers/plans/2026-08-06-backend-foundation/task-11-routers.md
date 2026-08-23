# Task 11 · Router — auth, appointments, shop

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Rewrite: `app/api/v1/routers/auth.py`
- Create: `app/api/v1/routers/appointments.py`, `app/api/v1/routers/shop.py`
- Modify: `app/api/v1/routers/__init__.py`, `main.py`
- Create: `tests/test_api_flow.py`

**Interfaces:**
- Consumes: mọi service từ Task 6, 8, 9; schema và dependency từ Task 10
- Produces: REST endpoint
  - `POST /api/v1/auth/login`, `POST /api/v1/auth/reset-password`, `POST /api/v1/auth/users` (admin), `GET /api/v1/auth/users` (admin), `GET /api/v1/auth/me`
  - `POST /api/v1/appointments`, `GET /api/v1/appointments/mine`, `DELETE /api/v1/appointments/{id}`, `GET /api/v1/appointments/day/{day}` (admin), `GET /api/v1/appointments/free-slots/{day}`
  - `GET /api/v1/shop/status`, `POST /api/v1/shop/busy` (admin), `POST /api/v1/shop/free` (admin), `GET|PUT /api/v1/shop/hours`
  - Exception handler dịch `AppError` → HTTP, và `PyMongoError` → 503 kèm SĐT tiệm

- [ ] **Step 1: Viết test đầu-cuối (sẽ fail)**

Tạo `tests/test_api_flow.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_api_flow.py -v`
Expected: FAIL — `/api/v1/auth/login` vẫn nhận `username`, các route khác chưa tồn tại

- [ ] **Step 3: Viết lại `app/api/v1/routers/auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (CreateUserRequest, LoginRequest,
                                ResetPasswordRequest, TokenResponse,
                                UserResponse)
from app.core.security import create_access_token
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id), phone=user.phone, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    user = await AuthService(db).authenticate(payload.phone, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Số điện thoại hoặc mật khẩu không đúng")
    return TokenResponse(access_token=create_access_token({"sub": str(user.id)}), role=user.role)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest, request: Request, db: AsyncIOMotorDatabase = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    await AuthService(db).reset_password(payload.phone, payload.new_password, ip=ip)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    user = await AuthService(db).create_user(
        payload.phone, payload.password, payload.full_name, payload.role
    )
    return _to_response(user)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return [_to_response(u) for u in await UserRepository(db).list_all()]


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return _to_response(user)
```

- [ ] **Step 4: Viết `app/api/v1/routers/appointments.py`**

```python
from datetime import date

from fastapi import APIRouter, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (AppointmentCreateRequest, AppointmentResponse,
                                FreeSlotsResponse)
from app.models.appointment import Appointment
from app.models.user import User
from app.services.appointment import AppointmentService

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _to_response(appt: Appointment) -> AppointmentResponse:
    return AppointmentResponse(
        id=str(appt.id), start_at=appt.start_at, duration_minutes=appt.duration_minutes,
        note=appt.note, status=appt.status, user_name=appt.user_name, phone=appt.phone,
    )


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    created_via = "admin" if user.role == "admin" else "chat"
    appt = await AppointmentService(db).create(user, payload.start_at, payload.note, created_via)
    return _to_response(appt)


@router.get("/mine", response_model=list[AppointmentResponse])
async def my_appointments(
    user: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return [_to_response(a) for a in await AppointmentService(db).upcoming_for(user)]


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    appointment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    await AppointmentService(db).cancel(user, appointment_id)


@router.get("/day/{day}", response_model=list[AppointmentResponse])
async def day_schedule(
    day: date, _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return [_to_response(a) for a in await AppointmentService(db).day_schedule(day)]


@router.get("/free-slots/{day}", response_model=FreeSlotsResponse)
async def free_slots(
    day: date, _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return FreeSlotsResponse(slots=await AppointmentService(db).find_free_slots(day))
```

- [ ] **Step 5: Viết `app/api/v1/routers/shop.py`**

```python
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (SetBusyRequest, ShopHoursRequest,
                                ShopStatusResponse)
from app.models.shop import ShopHours
from app.models.user import User
from app.services.shop import ShopService

router = APIRouter(prefix="/shop", tags=["shop"])


@router.get("/status", response_model=ShopStatusResponse)
async def get_status(
    _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return ShopStatusResponse(**(await ShopService(db).get_status()).model_dump())


@router.post("/busy", response_model=ShopStatusResponse)
async def set_busy(
    payload: SetBusyRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ShopStatusResponse(**(await ShopService(db).set_busy(payload.minutes)).model_dump())


@router.post("/free", response_model=ShopStatusResponse)
async def set_free(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return ShopStatusResponse(**(await ShopService(db).set_free()).model_dump())


@router.get("/hours", response_model=ShopHours)
async def get_hours(
    _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await ShopService(db).get_hours()


@router.put("/hours", response_model=ShopHours)
async def set_hours(
    payload: ShopHoursRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ShopService(db).set_hours(
        payload.open_time, payload.close_time, payload.closed_days
    )
```

- [ ] **Step 6: Đăng ký router và hai exception handler**

Trong `app/api/v1/routers/__init__.py`, thay phần include router `tasks`/`conversations` bằng:

```python
from fastapi import APIRouter

from app.api.v1.routers import appointments, auth, health, shop

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(appointments.router)
api_router.include_router(shop.router)
```

Trong `main.py`, thêm handler dịch lỗi nghiệp vụ sang HTTP (đặt sau khi tạo `app`):

```python
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Người dùng không bao giờ thấy lỗi kỹ thuật — mọi lỗi thành một câu tiếng Việt."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
```

Và ngay dưới, handler cho trường hợp **Mongo chết**. Đây là fail-hard duy nhất trong cả
hệ: mất Mongo là mất toàn bộ lịch, không có đường chạy giảm cấp. Việc duy nhất còn làm
được cho khách là đưa số điện thoại tiệm để gọi người thật.

```python
import logging

from pymongo.errors import PyMongoError

from app.core.config import settings

logger = logging.getLogger(__name__)


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError):
    """Mongo chết → 503 kèm SĐT tiệm. Traceback chỉ vào log, không ra màn hình.

    `PyMongoError` là lớp cha của `ServerSelectionTimeoutError`, `AutoReconnect`
    và `NetworkTimeout` — bắt lớp cha để không phải liệt kê thiếu.
    """
    logger.exception("mongo_unavailable", extra={"path": request.url.path})
    phone = settings.shop_phone or "tiệm"
    return JSONResponse(
        status_code=503,
        content={
            "detail": f"Máy của tiệm đang hỏng ạ. Cô chú gọi giúp con số {phone} nhé.",
            "shop_phone": settings.shop_phone,
        },
    )
```

**Cạm bẫy:** `DuplicateKeyError` cũng là lớp con của `PyMongoError`. Repo ở task 7 đã bắt
nó và đổi thành `SlotTakenError`, nên trùng giờ vẫn ra 409 chứ không rơi vào handler này.
Nếu sau này thêm chỗ ghi Mongo mới mà quên bắt, khách sẽ thấy "máy của tiệm đang hỏng"
trong khi thật ra chỉ là trùng giờ — test `test_double_booking_returns_409` ở trên là
lưới an toàn cho đúng chuyện đó.

- [ ] **Step 7: Chạy test để xác nhận pass**

Run: `pytest tests/test_api_flow.py -v`
Expected: PASS (9 passed)

- [ ] **Step 8: Chạy toàn bộ test**

Run: `pytest -v`
Expected: PASS toàn bộ. Nếu `tests/test_chat.py` fail vì tham chiếu AutoGen/task cũ, xóa nó: `git rm tests/test_chat.py` (chat sẽ được viết lại ở Plan 2).

- [ ] **Step 9: Commit**

```bash
git add app/api main.py tests/
git commit -m "feat: REST API for auth, appointments and shop status"
```

---
