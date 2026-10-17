from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.models.proposal import Proposal
from app.repositories.base import BaseRepository


def _oid(proposal_id: str) -> Optional[ObjectId]:
    return ObjectId(proposal_id) if ObjectId.is_valid(proposal_id or "") else None


class ProposalRepository(BaseRepository[Proposal]):
    """Mọi lệnh đổi trạng thái đều có ĐIỀU KIỆN trạng thái cũ trong câu lọc —
    nguyên tử trên một document, không cần transaction (mongo standalone)."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Proposal, "proposals")

    async def insert(self, data: Dict[str, Any], *, now: datetime) -> Proposal:
        """Như `BaseRepository.create` nhưng mốc `created_at` do service cấp —
        cùng mốc với `expires_at`, nên hạn luôn đúng 10 phút kể từ lúc tạo."""
        data = {**data, "created_at": now, "updated_at": now}
        result = await self.collection.insert_one(data)
        return Proposal(**{**data, "_id": result.inserted_id})

    async def supersede_pending(self, user_id: str) -> int:
        result = await self.collection.update_many(
            {"user_id": user_id, "status": "pending"},
            {"$set": {"status": "superseded"}},
        )
        return result.modified_count

    async def get_owned(self, user_id: str, proposal_id: str) -> Optional[Proposal]:
        oid = _oid(proposal_id)
        if oid is None:
            return None
        doc = await self.collection.find_one({"_id": oid, "user_id": user_id})
        return Proposal(**doc) if doc else None

    async def claim(self, user_id: str, proposal_id: str, *, now: datetime,
                    turn_at: datetime) -> Optional[Proposal]:
        """pending → approving, chỉ khi: đúng chủ, còn hạn, và tạo TRƯỚC lượt duyệt.
        Hai lần duyệt chạy chồng thì đúng một lần claim được."""
        oid = _oid(proposal_id)
        if oid is None:
            return None
        doc = await self.collection.find_one_and_update(
            {"_id": oid, "user_id": user_id, "status": "pending",
             "expires_at": {"$gt": now}, "created_at": {"$lt": turn_at}},
            {"$set": {"status": "approving", "approving_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return Proposal(**doc) if doc else None

    async def transition(self, proposal_id, from_status: str, to_status: str,
                         extra: Optional[Dict[str, Any]] = None,
                         match: Optional[Dict[str, Any]] = None) -> bool:
        oid = proposal_id if isinstance(proposal_id, ObjectId) else _oid(proposal_id)
        if oid is None:
            return False
        result = await self.collection.update_one(
            {"_id": oid, "status": from_status, **(match or {})},
            {"$set": {"status": to_status, **(extra or {})}},
        )
        return result.modified_count == 1

    async def increment_unclear(self, user_id: str, proposal_id: str) -> Optional[Proposal]:
        oid = _oid(proposal_id)
        if oid is None:
            return None
        doc = await self.collection.find_one_and_update(
            {"_id": oid, "user_id": user_id, "status": "pending"},
            {"$inc": {"unclear_count": 1}},
            return_document=ReturnDocument.AFTER,
        )
        return Proposal(**doc) if doc else None
