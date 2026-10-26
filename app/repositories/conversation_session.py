"""Repository cho `conversation_sessions` (Task 14): bản nén của từng NGÀY đã
qua, dùng để dựng dòng "Lần trước" trong khối bối cảnh. Khác với
`ConversationService` (document `conversations`, 1 bản/khách, ghi đè theo
ngày), đây là 1 document/khách/NGÀY, không bao giờ bị ghi đè — unique index
`(user_id, day)` đảm bảo mỗi ngày chỉ có đúng một bản (bản MỚI NHẤT của ngày
đó thắng qua upsert).
"""
from datetime import date, datetime, timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.conversation import ConversationSession, ConversationSlots, StateDigest


def _as_datetime(day: date) -> datetime:
    # Mongo không có kiểu date riêng — lưu dạng datetime nửa đêm, cùng quy
    # ước với `ConversationState.day` (ConversationService.set_state).
    return datetime(day.year, day.month, day.day)


class ConversationSessionRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["conversation_sessions"]

    async def upsert(
        self,
        *,
        user_id: str,
        day: date,
        digest: Optional[StateDigest],
        slots: Optional[ConversationSlots],
        expires_at: datetime,
    ) -> None:
        """Ghi/đè bản nén của NGÀY `day` cho khách này. Gọi mỗi khi `_compact`
        nén thành công cho ngày đó — lượt nén sau trong CÙNG ngày ghi đè lượt
        trước (khoá `(user_id, day)`), không tích luỹ nhiều bản một ngày."""
        await self.collection.update_one(
            {"user_id": user_id, "day": _as_datetime(day)},
            {"$set": {
                "user_id": user_id,
                "day": _as_datetime(day),
                "digest": digest.model_dump() if digest else None,
                "slots": slots.model_dump() if slots else None,
                "expires_at": expires_at,
            }},
            upsert=True,
        )

    async def most_recent_before(
        self, user_id: str, today: date, within_days: int = 7,
    ) -> Optional[ConversationSession]:
        """Phiên gần nhất của khách này, NGÀY NÀO ĐÓ TRƯỚC `today` (không bao
        giờ trả chính `today`), trong vòng `within_days` ngày gần đây. Không
        có thì trả None — chỗ gọi (context.py) bỏ hẳn dòng "Lần trước"."""
        start = _as_datetime(today - timedelta(days=within_days))
        end = _as_datetime(today)
        doc = await self.collection.find_one(
            {"user_id": user_id, "day": {"$gte": start, "$lt": end}},
            sort=[("day", -1)],
        )
        return ConversationSession(**doc) if doc else None
