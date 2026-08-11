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
        """Ghi TRƯỚC rồi mới đếm — thứ tự này là cố ý.

        Đếm trước rồi ghi thì giữa hai thao tác có một await, và mọi request
        đang chờ đều đọc được cùng một con số cũ: 20 request đồng thời cùng
        thấy count=0, cùng kết luận chưa chạm ngưỡng, rồi mới lần lượt ghi —
        cả 20 đều lọt. Uvicorn chạy 1 worker nhưng là async, nên đúng đủ điều
        kiện xảy ra. Đảo lại thì mỗi request đã ghi dấu của mình trước khi đếm,
        nên burst tự đếm cả chính nó và bị chặn.

        Đánh đổi đã cân nhắc: cửa sổ tính cả những lần đã bị từ chối, và một
        burst sẽ khoá luôn người dùng thật đến hết cửa sổ. Với chức năng đổi
        mật khẩu thì chặn nhầm an toàn hơn cho lọt.
        """
        now = now_utc()
        window_start = now - timedelta(seconds=window_seconds)

        await self.collection.insert_one({
            "key": key,
            "created_at": now,
            "expires_at": now + timedelta(seconds=window_seconds),
        })

        recent = await self.collection.count_documents(
            {"key": key, "created_at": {"$gte": window_start}}
        )
        if recent > limit:
            raise RateLimitedError()
