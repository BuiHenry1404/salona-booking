"""Phiên chờ của agent con (spec §5, luật 7 docs/architech-agent.md). Mặt tiền
cho agent con/Orchestrator — không có logic nghiệp vụ nào khác ngoài đọc/ghi
qua repository; hết hạn chỉ do `active()` kiểm lúc đọc, không TTL Mongo.
"""
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.waiting_session import WaitingSession
from app.repositories.waiting_session import WaitingSessionRepository


class WaitingSessionService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repository = WaitingSessionRepository(db)

    async def active(self, user_id: str, *, now: datetime) -> dict[str, WaitingSession]:
        """Phiên còn hiệu lực của khách, khoá theo tên agent."""
        sessions = await self.repository.find_active(user_id, now=now)
        return {session.agent: session for session in sessions}

    async def open(self, session: WaitingSession) -> None:
        """Mở/thay phiên của `(session.user_id, session.agent)`."""
        await self.repository.upsert(session)

    async def close(self, user_id: str, agent: str, *, reason: str) -> None:
        await self.repository.close_one(user_id, agent, reason=reason)

    async def close_opened_in_turn(self, user_id: str, turn_id: str, *, reason: str) -> list[str]:
        return await self.repository.close_opened_in_turn(user_id, turn_id, reason=reason)
