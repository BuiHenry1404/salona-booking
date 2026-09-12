from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import local_day_bounds, now_utc, to_local
from app.models.conversation import ChatMessage, Conversation, DaySummary

# Ước lượng thô cho tiếng Việt: ~3 ký tự một token. Đủ chính xác để cắt lịch sử;
# đếm token thật cần tokenizer của model và không đáng cho việc này.
CHARS_PER_TOKEN = 3
DEFAULT_TOKEN_BUDGET = 1500

# Tin trong ngần này phút luôn được giữ, kể cả khi đã sang ngày mới — xem
# `ConversationService.history`.
CARRY_OVER_MINUTES = 30

# Độ dài dòng xem trước trong màn lịch sử.
PREVIEW_CHARS = 80


class ConversationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["conversations"]

    async def get_or_create(self, user_id: str) -> Conversation:
        doc = await self.collection.find_one({"user_id": user_id})
        if doc:
            return Conversation(**doc)
        now = now_utc()
        payload = {"user_id": user_id, "messages": [], "pending_confirmation": None,
                   "created_at": now, "updated_at": now}
        result = await self.collection.insert_one(payload)
        payload["_id"] = result.inserted_id
        return Conversation(**payload)

    async def append(self, user_id: str, role: str, content: str) -> None:
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"messages": ChatMessage(role=role, content=content).model_dump()},
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

    async def history(
        self, user_id: str, token_budget: int = DEFAULT_TOKEN_BUDGET
    ) -> List[ChatMessage]:
        """Lịch sử của PHIÊN HÔM NAY, cắt thêm theo ngân sách token.

        Hai lớp cắt, cả hai đều cần:

        1. **Theo ngày** — chỉ nạp tin của ngày hôm nay theo giờ Việt Nam. Lịch
           sử chat chỉ dùng để hiểu các tham chiếu trong cùng mạch nói ("giờ đó",
           "ừ", "đổi giúp cô"); những thứ đó không có nghĩa sau vài tuần. Mọi
           thông tin bền của khách — tên, SĐT, lịch sắp tới, trạng thái tiệm —
           đã nằm trong khối bối cảnh dựng bằng code ở mỗi lượt, không lấy từ
           đây. Giữ lịch sử vài tháng chỉ tốn token và khiến model tưởng chuyện
           tháng trước vừa mới xảy ra, vì prompt KHÔNG mang mốc thời gian của
           từng tin.

           Cắt theo giờ Việt Nam chứ không theo UTC: nửa đêm UTC là 7 giờ sáng ở
           VN, cắt đúng giữa buổi làm việc.

        2. **Theo ngân sách token** — một khách nói dài dòng chiếm gấp nhiều lần
           một khách nói cộc lốc, nên đếm lượt là sai đơn vị.

        Ngoại lệ nửa đêm: mọi tin trong 30 phút gần nhất luôn được giữ, kể cả khi
        chúng thuộc hôm qua. Không có nó thì khách nhắn 23:58, AI hỏi xác nhận,
        khách đáp "ừ" lúc 00:01 — và câu "ừ" mất sạch ngữ cảnh.

        Ranh giới là NGÀY TRÔI QUA, không phải lần đăng nhập. Đăng nhập do vòng
        đời cookie quyết định (30 ngày), không phải một mốc có nghĩa trong hội
        thoại: khách đăng nhập ba lần một buổi chiều vẫn là một mạch nói, còn
        khách giữ đăng nhập nửa năm thì không bao giờ có ranh giới nào.
        """
        all_messages = await self._all_messages(user_id)
        if not all_messages:
            return []

        day_start, _ = local_day_bounds(to_local(now_utc()).date())
        carry_over = now_utc() - timedelta(minutes=CARRY_OVER_MINUTES)
        cutoff = min(day_start, carry_over)

        messages = [m for m in all_messages if m.created_at >= cutoff]
        kept: List[ChatMessage] = []
        used = 0
        for message in reversed(messages):
            cost = max(1, len(message.content) // CHARS_PER_TOKEN)
            if used + cost > token_budget and kept:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        # Vòng lặp trên đi từ tin mới nhất lùi về, nên chỗ cắt có thể rơi
        # giữa một cặp và để lại câu ĐÁP mà không có câu HỎI sinh ra nó.
        # Model đọc câu đáp mồ côi thì mất mạch. 04-agent.md:99 chốt phải
        # giữ trọn cặp; bỏ đúng một tin là đủ. Nếu chỉ đủ một tin thì thà
        # trả rỗng còn hơn trả một câu đáp mồ côi.
        if kept and kept[0].role == "assistant":
            if len(kept) == 1 or len(kept) >= 4:
                kept.pop(0)
        return kept

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

    async def set_pending(self, user_id: str, payload: Optional[Dict[str, Any]]) -> None:
        value = {**payload, "asked_at": now_utc()} if payload else None
        await self.collection.update_one(
            {"user_id": user_id},
            {"$set": {"pending_confirmation": value, "updated_at": now_utc()},
             "$setOnInsert": {"user_id": user_id, "messages": [], "created_at": now_utc()}},
            upsert=True,
        )

    async def get_pending(
        self, user_id: str, max_age_minutes: int = 10
    ) -> Optional[Dict[str, Any]]:
        """Cờ hết hạn sau 10 phút: quá đó thì khách nói "ừ" cũng phải hỏi lại,
        vì nhiều khả năng họ đang nói về chuyện khác."""
        doc = await self.collection.find_one({"user_id": user_id})
        pending = (doc or {}).get("pending_confirmation")
        if not pending:
            return None

        asked_at = pending.get("asked_at")
        if asked_at is None:
            return None
        if asked_at.tzinfo is None:
            asked_at = asked_at.replace(tzinfo=now_utc().tzinfo)
        if now_utc() - asked_at > timedelta(minutes=max_age_minutes):
            return None
        return pending
