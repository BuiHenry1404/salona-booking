"""Repository cho `waiting_sessions` (spec §5). Unique index `(user_id,
agent)` — mở phiên mới cùng agent ghi đè phiên cũ.
"""
from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.clock import now_utc
from app.models.waiting_session import WaitingSession


class WaitingSessionRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["waiting_sessions"]

    async def upsert(self, session: WaitingSession) -> None:
        """Ghi/đè phiên của `(user_id, agent)`. Hai lượt mở cùng khoá có thể
        đua nhau trên index unique (cả hai thấy chưa có document, cùng thử
        insert) — `DuplicateKeyError` thì thử lại một lần; lần hai document đã
        tồn tại nên upsert chạy như update bình thường (CONTEXT.md bẫy 2)."""
        payload = session.model_dump(by_alias=True, exclude={"id", "created_at", "updated_at"})
        now = now_utc()
        filter_ = {"user_id": session.user_id, "agent": session.agent}
        update = {"$set": {**payload, "updated_at": now}, "$setOnInsert": {"created_at": now}}
        try:
            await self.collection.update_one(filter_, update, upsert=True)
        except DuplicateKeyError:
            await self.collection.update_one(filter_, update, upsert=True)

    async def find_active(self, user_id: str, *, now: datetime) -> list[WaitingSession]:
        cursor = self.collection.find(
            {"user_id": user_id, "status": "waiting", "expires_at": {"$gt": now}}
        )
        return [WaitingSession(**doc) async for doc in cursor]

    async def close_one(self, user_id: str, agent: str, *, reason: str) -> None:
        """Không có phiên chờ của agent này thì không làm gì (match 0)."""
        await self.collection.update_one(
            {"user_id": user_id, "agent": agent, "status": "waiting"},
            {"$set": {"status": "closed", "closed_reason": reason, "updated_at": now_utc()}},
        )

    async def close_opened_in_turn(self, user_id: str, turn_id: str, *, reason: str,
                                   keep: Optional[str] = None) -> list[str]:
        """Đóng mọi phiên `waiting` được mở/đổi ở lượt `turn_id`, trừ phiên của
        agent `keep` (câu mẫu code đã hỏi khách câu hỏi của phiên đó — Ruling
        R12); trả tên các agent vừa đóng."""
        filter_: dict = {"user_id": user_id, "opened_turn_id": turn_id, "status": "waiting"}
        if keep is not None:
            filter_["agent"] = {"$ne": keep}
        agents = [doc["agent"] async for doc in self.collection.find(filter_, {"agent": 1})]
        if agents:
            await self.collection.update_many(
                filter_,
                {"$set": {"status": "closed", "closed_reason": reason, "updated_at": now_utc()}},
            )
        return agents
