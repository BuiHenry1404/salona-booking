from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.clock import local_day_bounds, now_utc, to_local
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
            {"$set": {"status": "superseded", "updated_at": now_utc()}},
        )
        return result.modified_count

    async def latest_for_user(self, user_id: str) -> Optional[Proposal]:
        """Đề xuất MỚI NHẤT của khách (theo lúc tạo), bất kể trạng thái."""
        doc = await self.collection.find_one({"user_id": user_id},
                                             sort=[("created_at", -1)])
        return Proposal(**doc) if doc else None

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
            # `updated_at` = lúc đổi trạng thái: khối bối cảnh dùng nó để biết
            # khách KHÔNG chốt từ khi nào (dòng "[declined: …]", lỗi live 3/6).
            {"$set": {"status": to_status, "updated_at": now_utc(), **(extra or {})}},
        )
        return result.modified_count == 1

    async def closed_earlier_today(self, user_id: str, before: datetime) -> List[Proposal]:
        """Task 15 review: đề xuất của khách CÙNG NGÀY giờ VN với `before` (đề
        xuất vừa được duyệt), TẠO TRƯỚC nó, và đã ĐÓNG mà khách KHÔNG chốt
        (`rejected`/`superseded`/`expired` — không tính `approved`/`failed`/
        `approving`/`pending`). `confirm.py` suy `confirm_fact["context"]` từ
        danh sách này: có cái START_AT KHÁC đề xuất vừa duyệt → khách đã đổi
        giờ ("changed_time"); có cái nào đó dù giờ không đổi → khách từng nói
        không/bỏ dở trước khi chốt ("after_decline").

        Thay cho `has_superseded_today_before` cũ (review: chỉ nhìn
        `superseded` thì bỏ sót trường hợp phổ biến hơn — khách bị VETO
        "giờ khác" đóng đề xuất cũ thành `rejected`, không phải `superseded`)."""
        start, _ = local_day_bounds(to_local(before).date())
        cursor = self.collection.find({
            "user_id": user_id, "status": {"$in": ["rejected", "superseded", "expired"]},
            "created_at": {"$gte": start, "$lt": before},
        })
        return [Proposal(**doc) async for doc in cursor]

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
