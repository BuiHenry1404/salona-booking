"""Nhật ký chat hiển thị (`conversations`, 1 document/khách) cho màn lịch sử
của khách. Lịch sử model đọc nằm ở thread checkpointer của Orchestrator, không
ở đây — `run_turn` chỉ ghi thêm vào nhật ký này sau mỗi lượt.
"""
from datetime import date, datetime
from typing import Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import local_day_bounds, now_utc, to_local
from app.models.conversation import ChatMessage, DaySummary

# Cuộc trò chuyện còn được dùng tiếp qua nửa đêm nếu lượt cuối cách chưa tới
# ngần này phút — xem `ConversationThreadService.current`.
CARRY_OVER_MINUTES = 30

# Độ dài dòng xem trước trong màn lịch sử.
PREVIEW_CHARS = 80


class ConversationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["conversations"]

    async def append(
        self, user_id: str, role: str, content: str, source: str = "llm",
        created_at: Optional[datetime] = None,
    ) -> None:
        """`created_at`: mốc của lượt (`now_ms()`), dùng chung với message
        trong thread checkpointer."""
        message = ChatMessage(role=role, content=content, source=source)
        if created_at is not None:
            message.created_at = created_at
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"messages": message.model_dump()},
                "$set": {"updated_at": now_utc()},
                "$setOnInsert": {"user_id": user_id, "created_at": now_utc()},
            },
            upsert=True,
        )

    async def _all_messages(self, user_id: str) -> List[ChatMessage]:
        doc = await self.collection.find_one({"user_id": user_id})
        if not doc:
            return []
        return [ChatMessage(**m) for m in doc.get("messages", [])]

    async def list_days(self, user_id: str) -> List[DaySummary]:
        """Các ngày khách từng nhắn, mới nhất trước.

        Gom theo ngày ĐỊA PHƯƠNG. Gom theo UTC thì mốc cắt rơi vào 7 giờ sáng
        giờ Việt Nam, xé đôi một buổi làm việc thành hai dòng trong danh sách.
        """
        buckets: Dict[date, List[ChatMessage]] = {}
        for message in await self._all_messages(user_id):
            buckets.setdefault(to_local(message.created_at).date(), []).append(message)

        summaries = []
        for day, messages in buckets.items():
            first_from_customer = next(
                (m.content for m in messages if m.role == "user"),
                messages[0].content,
            )
            summaries.append(
                DaySummary(
                    day=day,
                    message_count=len(messages),
                    preview=first_from_customer[:PREVIEW_CHARS],
                )
            )
        return sorted(summaries, key=lambda s: s.day, reverse=True)

    async def messages_on(self, user_id: str, day: date) -> List[ChatMessage]:
        """Toàn bộ tin của một ngày, KHÔNG cắt theo ngân sách token.

        Đây là màn đọc lại của khách, không phải thứ nhồi vào prompt — cắt bớt
        ở đây chỉ làm mất chữ khách đã nói.
        """
        start, end = local_day_bounds(day)
        return [
            m
            for m in await self._all_messages(user_id)
            if start <= m.created_at < end
        ]
