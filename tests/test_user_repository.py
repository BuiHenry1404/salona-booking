import pytest
from app.core.errors import PhoneTakenError
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
    # Không để DuplicateKeyError lọt ra: nó là PyMongoError nên handler sẽ trả
    # 503 "máy của tiệm đang hỏng" cho một lỗi nhập liệu bình thường.
    with pytest.raises(PhoneTakenError):
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
