# Task 4 · Model và repository User theo số điện thoại

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Modify: `app/models/user.py`, `app/repositories/user.py`, `app/infrastructure/database.py`
- Create: `tests/test_user_repository.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `normalize_phone` (Task 2), `BaseRepository` (có sẵn)
- Produces:
  - `User(phone: str, hashed_password: str, full_name: str | None, role: Literal["user","admin"], is_active: bool)`
  - `CurrentUser(id, phone, full_name, role, is_active)`
  - `UserRepository.get_by_phone(phone: str) -> User | None`
  - `UserRepository.create_user(phone, hashed_password, full_name, role) -> User`
  - `UserRepository.set_password(user_id: str, hashed_password: str) -> bool`
  - `UserRepository.list_all() -> list[User]`
  - `ensure_indexes(db) -> None` trong `app/infrastructure/database.py`

- [ ] **Step 1: Thay fixture cũ trong conftest**

Trong `tests/conftest.py`, **xóa toàn bộ** fixture `test_user` (nó dùng email/username và endpoint `/register` sắp bị bỏ), rồi thêm vào cuối file:

```python
import pytest_asyncio

from app.infrastructure.database import ensure_indexes


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_db):
    """Mỗi test bắt đầu từ DB trống nhưng đã có index."""
    for name in await test_db.list_collection_names():
        await test_db[name].drop()
    await ensure_indexes(test_db)
    yield
```

Thêm vào đầu file (sau các import sẵn có):

```python
pytest_plugins = ("pytest_asyncio",)
```

- [ ] **Step 2: Viết test (sẽ fail)**

Tạo `tests/test_user_repository.py`:

```python
import pytest
from pymongo.errors import DuplicateKeyError

from app.repositories.user import UserRepository

pytestmark = pytest.mark.asyncio


async def test_create_and_fetch_by_phone(test_db):
    repo = UserRepository(test_db)
    await repo.create_user(phone="0912345678", hashed_password="h", full_name="Cô Lan", role="user")

    found = await repo.get_by_phone("0912345678")
    assert found is not None
    assert found.full_name == "Cô Lan"
    assert found.role == "user"
    assert found.is_active is True


async def test_phone_is_unique(test_db):
    repo = UserRepository(test_db)
    await repo.create_user(phone="0912345678", hashed_password="h", full_name="A", role="user")
    with pytest.raises(DuplicateKeyError):
        await repo.create_user(phone="0912345678", hashed_password="h", full_name="B", role="user")


async def test_lookup_normalises_the_phone(test_db):
    repo = UserRepository(test_db)
    await repo.create_user(phone="0912345678", hashed_password="h", full_name="A", role="user")
    assert await repo.get_by_phone("+84 912 345 678") is not None


async def test_unknown_phone_returns_none(test_db):
    repo = UserRepository(test_db)
    assert await repo.get_by_phone("0900000000") is None


async def test_set_password(test_db):
    repo = UserRepository(test_db)
    user = await repo.create_user(phone="0912345678", hashed_password="old", full_name="A", role="user")
    assert await repo.set_password(str(user.id), "new") is True
    assert (await repo.get_by_phone("0912345678")).hashed_password == "new"
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_user_repository.py -v`
Expected: FAIL — `User` vẫn đòi `email`/`username`

- [ ] **Step 4: Viết lại `app/models/user.py`**

```python
from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.models.base import BaseDocument

Role = Literal["user", "admin"]


class User(BaseDocument):
    """Khách hoặc chủ tiệm. Định danh bằng số điện thoại, không dùng email."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "phone": "0912345678",
                "full_name": "Nguyễn Thị Lan",
                "role": "user",
                "is_active": True,
            }
        },
    )

    phone: str = Field(..., min_length=10, max_length=10)
    hashed_password: str
    full_name: Optional[str] = None
    role: Role = "user"
    is_active: bool = True


class CurrentUser(BaseDocument):
    """User đã xác thực, không mang trường nhạy cảm."""

    phone: str
    full_name: Optional[str] = None
    role: Role
    is_active: bool
```

- [ ] **Step 5: Viết lại `app/repositories/user.py`**

```python
from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.phone import InvalidPhoneError, normalize_phone
from app.models.user import Role, User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, User, "users")

    async def get_by_phone(self, phone: str) -> Optional[User]:
        try:
            normalised = normalize_phone(phone)
        except InvalidPhoneError:
            return None
        doc = await self.collection.find_one({"phone": normalised})
        return User(**doc) if doc else None

    async def create_user(
        self, phone: str, hashed_password: str, full_name: Optional[str], role: Role = "user"
    ) -> User:
        return await self.create({
            "phone": normalize_phone(phone),
            "hashed_password": hashed_password,
            "full_name": full_name,
            "role": role,
            "is_active": True,
        })

    async def set_password(self, user_id: str, hashed_password: str) -> bool:
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id)}, {"$set": {"hashed_password": hashed_password}}
        )
        return result.matched_count == 1

    async def list_all(self) -> List[User]:
        docs = await self.collection.find().sort("full_name", 1).to_list(length=500)
        return [User(**d) for d in docs]
```

- [ ] **Step 6: Thêm `ensure_indexes` vào `app/infrastructure/database.py`**

Thêm vào cuối file:

```python
from motor.motor_asyncio import AsyncIOMotorDatabase


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Tạo mọi index. Gọi lúc khởi động app và trong fixture test.

    Index trên slot_keys BẮT BUỘC là partial lọc status="booked".
    Không được dùng sparse: MongoDB đánh index mảng rỗng thành undefined, nên
    lịch bị hủy thứ hai sẽ ném E11000 dup key: { : undefined }.
    """
    await db["users"].create_index("phone", unique=True)

    await db["appointments"].create_index(
        "slot_keys",
        unique=True,
        partialFilterExpression={"status": "booked"},
        name="uniq_active_slot_keys",
    )
    await db["appointments"].create_index("start_at")
    await db["appointments"].create_index([("user_id", 1), ("start_at", 1)])

    await db["rate_limits"].create_index("expires_at", expireAfterSeconds=0)
    await db["rate_limits"].create_index([("key", 1), ("created_at", 1)])
```

- [ ] **Step 7: Gọi `ensure_indexes` lúc khởi động**

Trong `main.py`, tìm hàm lifespan/startup đang kết nối Mongo và thêm ngay sau khi có `db`:

```python
    from app.infrastructure.database import ensure_indexes
    await ensure_indexes(db)
```

- [ ] **Step 8: Chạy test để xác nhận pass**

Run: `pytest tests/test_user_repository.py -v`
Expected: PASS (5 passed)

- [ ] **Step 9: Commit**

```bash
git add app/models/user.py app/repositories/user.py app/infrastructure/database.py tests/
git commit -m "feat: identify users by phone number instead of email"
```

---
