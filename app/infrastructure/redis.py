"""Kết nối Redis tuỳ chọn cho dedupe tin nhắn (Task 10).

Khởi tạo LƯỜI: client chỉ được tạo lần đầu `get_redis()` được gọi, và chỉ khi
`settings.redis_url` có giá trị. Không đặt (comment trong .env, hoặc rỗng —
coi rỗng như tắt luôn cho an toàn, dù AGENTS.md chỉ nói comment mới là tắt) thì
trả `None` mãi mãi — Mongo fallback (`app/services/dedupe.py`) là đường chạy
mặc định ở dev, không phải một chế độ suy giảm.

Lỗi kết nối/ping không bao giờ làm app chết (khác Mongo, luôn fail-hard):
`MessageDedupe` tự bắt lỗi của backend Redis ở MỖI lời gọi và rơi xuống Mongo
cho lời gọi đó, nên một lần ping thất bại lúc khởi động chỉ đáng một dòng
warning, không đáng dừng app.
"""
from typing import Optional

import redis.asyncio as redis_asyncio

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Optional["redis_asyncio.Redis"] = None
_attempted = False


def get_redis() -> Optional["redis_asyncio.Redis"]:
    """Trả về client Redis (tạo lười lần gọi đầu), hoặc `None` nếu tắt."""
    global _client, _attempted
    if _attempted:
        return _client
    _attempted = True
    url = (settings.redis_url or "").strip()
    if not url:
        _client = None
        return None
    _client = redis_asyncio.from_url(url, decode_responses=True)
    return _client


async def ping_redis() -> bool:
    """Gọi lúc lifespan: thử nối Redis, lỗi thì warning và coi như tắt.

    KHÔNG fail-hard — khác hẳn Mongo. Trả `False` khi không cấu hình hoặc khi
    ping lỗi; không ném ngoại lệ ra ngoài trong cả hai trường hợp.
    """
    client = get_redis()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception as exc:
        logger.warning("redis_ping_failed", error=str(exc), error_type=type(exc).__name__)
        return False


def reset_redis_for_tests() -> None:
    """Xoá cache lười để test đổi `settings.redis_url` rồi gọi lại `get_redis()`."""
    global _client, _attempted
    _client = None
    _attempted = False
