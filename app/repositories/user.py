from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.errors import PhoneTakenError
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
        """DuplicateKeyError phải được dịch ngay tại đây.

        Nó là lớp con của PyMongoError, mà handler PyMongoError trả 503 "máy của
        tiệm đang hỏng". Để lọt ra thì admin tạo trùng SĐT sẽ bị báo hỏng máy
        thay vì "số này đã có tài khoản". Kiểm tra trước khi ghi ở service
        không đủ: hai admin bấm cùng lúc vẫn qua được cửa đó.
        """
        try:
            return await self.create({
                "phone": normalize_phone(phone),
                "hashed_password": hashed_password,
                "full_name": full_name,
                "role": role,
                "is_active": True,
            })
        except DuplicateKeyError:
            raise PhoneTakenError()

    async def set_password(self, user_id: str, hashed_password: str) -> bool:
        """Tăng `token_version` cùng lúc để thu hồi mọi token đã phát trước đó."""
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"hashed_password": hashed_password}, "$inc": {"token_version": 1}},
        )
        return result.matched_count == 1

    async def list_all(self) -> List[User]:
        docs = await self.collection.find().sort("full_name", 1).to_list(length=500)
        return [User(**d) for d in docs]
