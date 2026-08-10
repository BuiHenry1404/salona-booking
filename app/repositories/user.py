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
