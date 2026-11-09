"""Vòng đời cuộc trò chuyện (spec §3.4, luật 8 docs/architech-agent.md).
`thread_id` của checkpointer Orchestrator là `str(thread.id)`.
"""
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import to_local
from app.models.conversation_thread import ConversationThread
from app.repositories.conversation_thread import ConversationThreadRepository
from app.services.conversation import CARRY_OVER_MINUTES


class ConversationThreadService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repository = ConversationThreadRepository(db)

    async def current(self, user_id: str, now: datetime) -> tuple[ConversationThread, bool]:
        """Cuộc trò chuyện khách đang dùng ở `now`, và cờ vừa mở cuộc mới.

        Dùng tiếp cuộc mới nhất nếu cùng ngày VN với `now`, hoặc lượt cuối của
        nó cách `now` chưa tới `CARRY_OVER_MINUTES` (ca nửa đêm). Ngược lại mở
        cuộc mới, `previous_id` trỏ về cuộc cũ. Luôn đặt `last_turn_at = now`.
        """
        previous = await self.repository.most_recent(user_id)
        if previous is not None and (
            previous.day == to_local(now).date()
            or now - previous.last_turn_at < timedelta(minutes=CARRY_OVER_MINUTES)
        ):
            await self.repository.touch(str(previous.id), last_turn_at=now)
            return previous.model_copy(update={"last_turn_at": now}), False

        created = await self.repository.create(
            user_id=user_id,
            day=to_local(now).date(),
            started_at=now,
            previous_id=str(previous.id) if previous is not None else None,
        )
        return created, True
