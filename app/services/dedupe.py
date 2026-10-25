"""Dedupe tin nhắn Socket.IO (Task 10).

Socket.IO có thể gửi lại cùng một tin (client retry/reconnect); không có gì
chặn thì server chạy một lượt agent MỚI cho tin đã xử lý — tốn tiền LLM, có
thể tạo hai đề xuất/hai lịch. Client (Task 6) gửi kèm `message_id` (uuid) ở
mọi `send_message`/`confirm_proposal`; dedupe khoá theo cặp `(user_id,
message_id)`.

Hai backend:
- `RedisBackend`: SET NX để giữ "đang chạy" (TTL ngắn, chỉ đủ một lượt), lưu
  payload `complete` riêng (TTL dài) để replay.
- `MongoBackend`: một document mỗi `(user_id, message_id)`, unique index
  (xem `ensure_indexes`) làm việc giữ chỗ nguyên tử — hai lượt insert cùng
  cặp chỉ một cái thắng, cái thua đọc lại document đã có.

`MessageDedupe` ưu tiên Redis, lỗi ở BẤT KỲ lời gọi nào (mất kết nối giữa
chừng, không chỉ lúc khởi động) đều rơi xuống Mongo CHO LỜI GỌI ĐÓ — không ghim
vĩnh viễn, lần gọi sau vẫn thử lại Redis trước.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal, Optional, Union

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.clock import now_utc
from app.core.logging import get_logger

logger = get_logger(__name__)

# Khoá "đang chạy": chỉ cần sống hết một lượt (timeout lượt là 60s, xem
# events.TURN_TIMEOUT_SECONDS) — rộng rãi hơn một chút để tự giải phóng nếu
# `abandon` lỡ không chạy tới (crash tiến trình giữa chừng).
TURN_TTL_SECONDS = 90
# Payload `complete` lưu lại để replay — cùng hạn với TTL của `message_inbox`.
PAYLOAD_TTL_SECONDS = 24 * 3600

BeginResult = Union[Literal["new", "running"], dict]


class RedisBackend:
    """`turn:{user}:{message}` giữ chỗ đang chạy; `msg:{user}:{message}` giữ
    payload đã xong. Nhận một client Redis (hoặc fake trong test) có đúng ba
    thao tác: `set(name, value, nx=, ex=)`, `get(name)`, `delete(name)`."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _turn_key(user_id: str, message_id: str) -> str:
        return f"turn:{user_id}:{message_id}"

    @staticmethod
    def _msg_key(user_id: str, message_id: str) -> str:
        return f"msg:{user_id}:{message_id}"

    async def begin(self, user_id: str, message_id: str) -> BeginResult:
        done = await self.client.get(self._msg_key(user_id, message_id))
        if done is not None:
            return json.loads(done)
        acquired = await self.client.set(
            self._turn_key(user_id, message_id), "1", nx=True, ex=TURN_TTL_SECONDS
        )
        return "new" if acquired else "running"

    async def finish(self, user_id: str, message_id: str, complete_payload: dict) -> None:
        await self.client.set(
            self._msg_key(user_id, message_id),
            json.dumps(complete_payload),
            ex=PAYLOAD_TTL_SECONDS,
        )
        await self.client.delete(self._turn_key(user_id, message_id))

    async def abandon(self, user_id: str, message_id: str) -> None:
        await self.client.delete(self._turn_key(user_id, message_id))


class MongoBackend:
    """`message_inbox`: insert giữ chỗ nguyên tử nhờ unique index Mongo —
    `DuplicateKeyError` được bắt NGAY TẠI ĐÂY (quy tắc repository), không bao
    giờ để lọt ra thành lỗi 503 "máy đang hỏng"."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["message_inbox"]

    async def begin(self, user_id: str, message_id: str) -> BeginResult:
        now = now_utc()
        try:
            await self.collection.insert_one({
                "user_id": user_id,
                "message_id": message_id,
                "status": "running",
                "complete": None,
                "created_at": now,
                "expires_at": now + timedelta(hours=24),
            })
            return "new"
        except DuplicateKeyError:
            doc = await self.collection.find_one(
                {"user_id": user_id, "message_id": message_id}
            )
            if doc is None:
                # TTL dọn đúng lúc giữa insert thất bại và đọc lại — hiếm,
                # coi như chưa từng có, xử lý như tin mới.
                return "new"
            if doc["status"] == "done":
                return doc.get("complete") or {}
            return "running"

    async def finish(self, user_id: str, message_id: str, complete_payload: dict) -> None:
        await self.collection.update_one(
            {"user_id": user_id, "message_id": message_id},
            {"$set": {"status": "done", "complete": complete_payload}},
        )

    async def abandon(self, user_id: str, message_id: str) -> None:
        # Chỉ xoá khi còn "running": một lượt chậm bị coi là bỏ sau khi một
        # lượt KHÁC đã kịp `finish` sẽ không xoá nhầm kết quả đã lưu.
        await self.collection.delete_one(
            {"user_id": user_id, "message_id": message_id, "status": "running"}
        )


class MessageDedupe:
    """Mặt tiền dùng trong `socketio_service`. `redis_client=None` (Redis chưa
    cấu hình, hoặc lỗi lúc ping khởi động) thì chạy thẳng Mongo, không thử
    Redis ở từng lời gọi — tạo client lúc đó rồi mới lỗi tốn thêm một round
    trip mỗi tin mà kết quả giống hệt."""

    def __init__(self, db: AsyncIOMotorDatabase, redis_client=None):
        self.fallback = MongoBackend(db)
        self.primary = RedisBackend(redis_client) if redis_client is not None else None

    async def _call(self, op: str, *args):
        if self.primary is not None:
            try:
                return await getattr(self.primary, op)(*args)
            except Exception as exc:
                logger.warning("dedupe_redis_failed", op=op, error=str(exc),
                              error_type=type(exc).__name__)
        return await getattr(self.fallback, op)(*args)

    async def begin(self, user_id: str, message_id: Optional[str]) -> BeginResult:
        """Thiếu `message_id` (client cũ, hoặc field rỗng) thì KHÔNG dedupe —
        trả "new" và không lưu gì, mỗi lượt đều chạy như trước Task 10."""
        if not message_id:
            return "new"
        return await self._call("begin", user_id, message_id)

    async def finish(self, user_id: str, message_id: Optional[str],
                      complete_payload: dict) -> None:
        if not message_id:
            return
        await self._call("finish", user_id, message_id, complete_payload)

    async def abandon(self, user_id: str, message_id: Optional[str]) -> None:
        if not message_id:
            return
        await self._call("abandon", user_id, message_id)
