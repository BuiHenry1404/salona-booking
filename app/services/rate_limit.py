from datetime import timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.core.errors import RateLimitedError


class RateLimitService:
    """Đếm số lần thử trong một cửa sổ thời gian, lưu ở Mongo.

    Không giữ trong bộ nhớ tiến trình: biến RAM mất khi restart và không dùng
    được nếu về sau chạy nhiều tiến trình. Stack không có Redis (xem README spec).
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["rate_limits"]

    async def check_and_hit(self, key: str, limit: int = 5, window_seconds: int = 3600) -> None:
        now = now_utc()
        window_start = now - timedelta(seconds=window_seconds)

        recent = await self.collection.count_documents(
            {"key": key, "created_at": {"$gte": window_start}}
        )
        if recent >= limit:
            raise RateLimitedError()

        await self.collection.insert_one({
            "key": key,
            "created_at": now,
            "expires_at": now + timedelta(seconds=window_seconds),
        })
