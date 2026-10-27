"""Kết nối Redis tuỳ chọn cho dedupe tin nhắn (Task 10).

Khởi tạo LƯỜI: client chỉ được tạo lần đầu `get_redis()` được gọi, và chỉ khi
`settings.redis_url` có giá trị. Không đặt (comment trong .env, hoặc rỗng —
coi rỗng như tắt luôn cho an toàn, dù AGENTS.md chỉ nói comment mới là tắt) thì
trả `None` mãi mãi — Mongo fallback (`app/services/dedupe.py`) là đường chạy
mặc định ở dev, không phải một chế độ suy giảm.

Hai tầng chịu lỗi KHÁC NHAU, đừng nhầm:
- `ping_redis()` (lúc lifespan) thất bại → TẮT HẲN Redis cho suốt vòng đời
  tiến trình (`get_redis()` trả `None` từ đó về sau, không thử lại) — review
  fix: trước đây ping lỗi chỉ log warning rồi vẫn phát client hỏng đó cho mọi
  lượt chat sau, mỗi lượt lại tự trả giá một lần timeout kết nối.
- `MessageDedupe` (per-call) vẫn tự bắt lỗi của backend Redis ở MỖI lời gọi
  và rơi xuống Mongo cho lời gọi đó — đây là tấm lưới cho một kết nối ĐANG
  SỐNG rồi rớt giữa chừng sau khi ping lúc khởi động đã qua, không phải lý do
  để bỏ việc tắt hẳn ở trên.

KHÔNG fail-hard như Mongo trong cả hai trường hợp — mất Redis chỉ đáng một
dòng warning, app chạy tiếp bằng Mongo.
"""
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as redis_asyncio

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Optional["redis_asyncio.Redis"] = None
_attempted = False
# True sau khi `ping_redis()` lỗi lúc lifespan — tắt Redis cho HẾT vòng đời
# tiến trình, không thử lại ở các lượt chat sau.
_disabled = False

# Một lượt chat có trần 60s (TURN_TIMEOUT_SECONDS); Redis chỉ đáng gọi nếu
# nhanh hơn thế NHIỀU — timeout ngắn để một Redis treo (không phải từ chối
# kết nối, mà treo im) không kéo cả lượt chat theo.
_SOCKET_CONNECT_TIMEOUT = 0.5
_SOCKET_TIMEOUT = 1.0


def get_redis() -> Optional["redis_asyncio.Redis"]:
    """Trả về client Redis (tạo lười lần gọi đầu), hoặc `None` nếu tắt/đã bị
    vô hiệu hoá vĩnh viễn sau một lần ping thất bại."""
    global _client, _attempted
    if _disabled:
        return None
    if _attempted:
        return _client
    _attempted = True
    url = (settings.redis_url or "").strip()
    if not url:
        _client = None
        return None
    _client = redis_asyncio.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT,
        socket_timeout=_SOCKET_TIMEOUT,
    )
    return _client


async def ping_redis() -> bool:
    """Gọi lúc lifespan: thử nối Redis, lỗi thì warning và TẮT HẲN Redis cho
    tiến trình này (`get_redis()` trả `None` từ nay — không có cơ chế thử lại
    định kỳ, một Redis "rớt lúc khởi động rồi sống lại sau" sẽ cần restart
    tiến trình, chấp nhận được vì Redis chỉ là tối ưu, Mongo mới là đường thật).

    KHÔNG fail-hard — khác hẳn Mongo. Trả `False` khi không cấu hình hoặc khi
    ping lỗi; không ném ngoại lệ ra ngoài trong cả hai trường hợp.
    """
    global _client, _disabled
    client = get_redis()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception as exc:
        logger.warning("redis_ping_failed", error=str(exc), error_type=type(exc).__name__)
        _disabled = True
        _client = None
        return False


async def cache_get_or_load(
    key: str,
    ttl_s: int,
    loader: Callable[[], Awaitable[Any]],
    *,
    encode: Callable[[Any], str],
    decode: Callable[[str], Any],
) -> Any:
    """Đọc `key` qua Redis trước, gọi `loader()` (Mongo) khi trống/lỗi/tắt.

    `encode`/`decode` do nơi gọi truyền vào (Task 11, shop status/hours) để
    hàm này ở lại JSON-thuần trong Redis mà vẫn giữ kiểu Pydantic/datetime an
    toàn phía gọi — không đoán cấu trúc dữ liệu ở đây.

    Redis `None` (tắt/chưa cấu hình) → gọi thẳng `loader()`, không set lại gì
    (set vào `None` cũng không làm được). Lỗi Redis ở BẤT KỲ bước nào (get hay
    set) → warning rồi tiếp tục bằng `loader()`, cùng kiểu chịu lỗi với
    `MessageDedupe` — không ghim tắt vĩnh viễn, lần gọi sau vẫn thử Redis lại.
    """
    client = get_redis()
    if client is not None:
        try:
            cached = await client.get(key)
            if cached is not None:
                return decode(cached)
        except Exception as exc:
            logger.warning(
                "shop_cache_get_failed", key=key, error=str(exc), error_type=type(exc).__name__
            )
            client = None

    value = await loader()

    if client is not None:
        try:
            await client.set(key, encode(value), ex=ttl_s)
        except Exception as exc:
            logger.warning(
                "shop_cache_set_failed", key=key, error=str(exc), error_type=type(exc).__name__
            )

    return value


async def cache_set(key: str, value: Any, ttl_s: int, *, encode: Callable[[Any], str]) -> None:
    """Ghi thẳng (write-through) `value` vào cache sau khi nơi gọi vừa ghi
    Mongo — dùng thay cho `cache_delete` ở chỗ cần tránh race "xoá rồi một bản
    đọc cũ hồi sinh cache" (Task 11 review fix, xem `ShopService.set_busy`).

    Redis tắt/lỗi → no-op (ngoài một dòng warning khi lỗi), Mongo vẫn đã là
    nguồn đúng."""
    client = get_redis()
    if client is None:
        return
    try:
        await client.set(key, encode(value), ex=ttl_s)
    except Exception as exc:
        logger.warning(
            "shop_cache_set_through_failed", key=key, error=str(exc), error_type=type(exc).__name__
        )


async def cache_delete(key: str) -> None:
    """Xoá `key` khỏi cache khi dữ liệu gốc vừa đổi (admin sửa trạng thái/giờ).

    Redis tắt/lỗi → no-op, dữ liệu gốc (Mongo) đã là nguồn đúng rồi."""
    client = get_redis()
    if client is None:
        return
    try:
        await client.delete(key)
    except Exception as exc:
        logger.warning(
            "shop_cache_delete_failed", key=key, error=str(exc), error_type=type(exc).__name__
        )


def reset_redis_for_tests() -> None:
    """Xoá cache lười (kể cả cờ tắt vĩnh viễn) để test đổi `settings.redis_url`
    rồi gọi lại `get_redis()`/`ping_redis()` từ đầu."""
    global _client, _attempted, _disabled
    _client = None
    _attempted = False
    _disabled = False
