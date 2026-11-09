"""Repository cho `conversation_threads` (spec §3.4) — mỗi document là MỘT
cuộc trò chuyện (một ngày VN, trừ ca nửa đêm). Khác hẳn `conversations` (lịch
sử chat nguyên văn, 1 document/khách) và `conversation_sessions` (bản nén
theo ngày của agent cũ) — không dùng lại hai collection đó.
"""
from datetime import date, datetime
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.models.conversation_thread import ConversationThread


def _as_datetime(day: date) -> datetime:
    # Mongo không có kiểu date riêng — lưu dạng datetime nửa đêm, cùng quy
    # ước với `ConversationState.day` / `ConversationSession.day`.
    return datetime(day.year, day.month, day.day)


class ConversationThreadRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["conversation_threads"]

    async def most_recent(self, user_id: str) -> Optional[ConversationThread]:
        doc = await self.collection.find_one(
            {"user_id": user_id}, sort=[("started_at", -1)]
        )
        return ConversationThread(**doc) if doc else None

    async def create(
        self, *, user_id: str, day: date, started_at: datetime,
        previous_id: Optional[str],
    ) -> ConversationThread:
        now = now_utc()
        payload = {
            "user_id": user_id,
            "day": _as_datetime(day),
            "started_at": started_at,
            "last_turn_at": started_at,
            "previous_id": previous_id,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.collection.insert_one(payload)
        payload["_id"] = result.inserted_id
        return ConversationThread(**payload)

    async def touch(self, thread_id: str, *, last_turn_at: datetime) -> None:
        await self.collection.update_one(
            {"_id": ObjectId(thread_id)},
            {"$set": {"last_turn_at": last_turn_at, "updated_at": now_utc()}},
        )
