from datetime import datetime
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, RefreshToken, "refresh_tokens")

    async def create_token(
        self, user_id: str, family_id: str, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        return await self.create({
            "user_id": user_id,
            "family_id": family_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "used_at": None,
        })

    async def get_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        doc = await self.collection.find_one({"token_hash": token_hash})
        return RefreshToken(**doc) if doc else None

    async def mark_used(self, token_id: str) -> bool:
        """Lọc thêm `used_at: None` ngay trong câu update.

        Hai request song song cùng trình một token thì chỉ một cái đặt được dấu;
        cái còn lại thấy `matched_count == 0` và biết mình đi vào nhánh ân hạn,
        thay vì cả hai cùng tưởng mình là lần dùng đầu tiên.
        """
        result = await self.collection.update_one(
            {"_id": ObjectId(token_id), "used_at": None},
            {"$set": {"used_at": now_utc()}},
        )
        return result.matched_count == 1

    async def delete_family(self, family_id: str) -> None:
        await self.collection.delete_many({"family_id": family_id})

    async def delete_for_user(self, user_id: str) -> None:
        await self.collection.delete_many({"user_id": user_id})
