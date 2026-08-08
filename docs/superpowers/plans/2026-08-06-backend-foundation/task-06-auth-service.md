# Task 6 · AuthService — đăng nhập SĐT, admin tạo tài khoản, quên mật khẩu

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Rewrite: `app/services/auth.py`
- Create: `tests/test_auth_service.py`
- Delete: `tests/test_auth.py` (viết cho email/username, không còn đúng)

**Interfaces:**
- Consumes: `UserRepository` (Task 4), `RateLimitService` (Task 5), `normalize_phone` (Task 2), `verify_password`/`get_password_hash` (có sẵn ở `app/core/security.py`)
- Produces:
  - `AuthService(db).authenticate(phone: str, password: str) -> User | None`
  - `AuthService(db).create_user(phone, password, full_name, role="user") -> User` — ném `AppError` nếu SĐT đã tồn tại
  - `AuthService(db).reset_password(phone: str, new_password: str, ip: str) -> None` — ném `NotFoundError` nếu không có SĐT, `RateLimitedError` nếu quá ngưỡng
  - `AuthService(db).get_user_by_id(user_id) -> User | None`

- [ ] **Step 1: Xóa test cũ và viết test mới (sẽ fail)**

```bash
git rm tests/test_auth.py
```

Tạo `tests/test_auth_service.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_auth_service.py -v`
Expected: FAIL — `AuthService.create_user` chưa tồn tại (chỉ có `register_user` nhận `UserCreate`)

- [ ] **Step 3: Viết lại `app/services/auth.py`**

```python
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.phone import normalize_phone
from app.core.security import get_password_hash, verify_password
from app.models.user import Role, User
from app.repositories.user import UserRepository
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)

RESET_LIMIT = 5
RESET_WINDOW_SECONDS = 3600


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.rate_limit = RateLimitService(db)

    async def authenticate(self, phone: str, password: str) -> Optional[User]:
        user = await self.user_repo.get_by_phone(phone)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    async def create_user(
        self, phone: str, password: str, full_name: Optional[str], role: Role = "user"
    ) -> User:
        """Chỉ admin gọi. Không có đăng ký tự do."""
        normalised = normalize_phone(phone)
        if await self.user_repo.get_by_phone(normalised):
            raise AppError("Số điện thoại này đã có tài khoản")
        return await self.user_repo.create_user(
            phone=normalised,
            hashed_password=get_password_hash(password),
            full_name=full_name,
            role=role,
        )

    async def reset_password(self, phone: str, new_password: str, ip: str) -> None:
        """Đúng luồng trong spec: có SĐT trong DB thì cho đổi.

        Rủi ro đã được chủ dự án cân nhắc và chấp nhận (xem README spec): ai biết
        SĐT cũng đổi được mật khẩu. Giảm thiểu bằng rate limit theo cả SĐT lẫn IP,
        và ghi log mọi lần đổi.
        """
        normalised = normalize_phone(phone)
        await self.rate_limit.check_and_hit(f"pwreset:phone:{normalised}", RESET_LIMIT, RESET_WINDOW_SECONDS)
        await self.rate_limit.check_and_hit(f"pwreset:ip:{ip}", RESET_LIMIT, RESET_WINDOW_SECONDS)

        user = await self.user_repo.get_by_phone(normalised)
        if not user:
            raise NotFoundError("Không tìm thấy tài khoản với số này")

        await self.user_repo.set_password(str(user.id), get_password_hash(new_password))
        logger.warning("password_reset", extra={"phone": normalised, "ip": ip})

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        return await self.user_repo.get_by_id(user_id)
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_auth_service.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/auth.py tests/
git commit -m "feat: phone login, admin-created accounts, rate-limited password reset"
```

---
