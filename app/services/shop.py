import json
import math
from datetime import datetime, timedelta
from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.infrastructure.redis import cache_get_or_load, cache_set
from app.models.shop import ShopHours, ShopStatusView

_SINGLETON = {"_id": "singleton"}

# Khoá cache — TTL ngắn cho status (đổi liên tục, admin bấm bận/rảnh), TTL dài
# hơn cho hours (gần như tĩnh, chỉ đổi khi admin sửa giờ mở cửa).
STATUS_CACHE_KEY = "shop:status"
STATUS_CACHE_TTL_SECONDS = 30
HOURS_CACHE_KEY = "shop:hours"
HOURS_CACHE_TTL_SECONDS = 600


def _encode_json(value: dict) -> str:
    return json.dumps(value)


def _decode_json(raw: str) -> dict:
    return json.loads(raw)


def _status_doc_to_raw(doc: dict) -> dict:
    """Dict JSON-hoá được từ document Mongo — CHỈ dữ liệu thô (`is_busy`,
    `busy_until`), không phải view đã tính (`minutes_left` sẽ cũ dần nếu bị
    cache cùng — quy tắc controller, xem `get_status`)."""
    busy_until = doc.get("busy_until")
    return {
        "is_busy": bool(doc.get("is_busy")),
        "busy_until": busy_until.isoformat() if busy_until is not None else None,
    }


def _status_raw_to_view(raw: dict) -> ShopStatusView:
    """Tính view từ dữ liệu thô TẠI THỜI ĐIỂM GỌI — kể cả khi `raw` đến từ
    cache, `minutes_left` vẫn so với đồng hồ hiện tại, không phải lúc ghi
    cache. Quá busy_until thì coi như rảnh mà không cần admin bấm lại — đây là
    cách hiện thực yêu cầu 'hết giờ tự về Rảnh'."""
    busy_until_raw = raw.get("busy_until")
    busy_until = datetime.fromisoformat(busy_until_raw) if busy_until_raw else None
    if not raw.get("is_busy") or busy_until is None:
        return ShopStatusView(is_busy=False)

    remaining = (busy_until - now_utc()).total_seconds()
    if remaining <= 0:
        return ShopStatusView(is_busy=False)

    return ShopStatusView(
        is_busy=True,
        busy_until=busy_until,
        minutes_left=max(1, math.ceil(remaining / 60)),
    )


def _hours_doc_to_raw(doc: dict) -> dict:
    return {k: v for k, v in (doc or {}).items() if k != "_id"}


class ShopService:
    """Giờ mở cửa và trạng thái bận/rảnh. Cả hai là document đơn lẻ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.hours = db["shop_hours"]
        self.status = db["shop_status"]

    async def _load_hours_raw(self) -> dict:
        doc = await self.hours.find_one(_SINGLETON)
        return _hours_doc_to_raw(doc or {})

    async def get_hours(self) -> ShopHours:
        raw = await cache_get_or_load(
            HOURS_CACHE_KEY,
            HOURS_CACHE_TTL_SECONDS,
            self._load_hours_raw,
            encode=_encode_json,
            decode=_decode_json,
        )
        return ShopHours(**raw)

    async def set_hours(self, open_time: str, close_time: str, closed_days: List[int]) -> ShopHours:
        hours = ShopHours(open_time=open_time, close_time=close_time, closed_days=closed_days)
        await self.hours.update_one(_SINGLETON, {"$set": hours.model_dump()}, upsert=True)
        # Ghi thẳng (write-through) bản vừa lưu thay vì xoá cache rồi để lần
        # đọc sau tự nạp lại: XOÁ có một khe hở — một `get_hours()` đang chạy
        # dở, đã đọc xong Mongo TRƯỚC lần ghi này nhưng chưa kịp set cache,
        # có thể hoàn tất SAU `cache_delete` và hồi sinh cache với dữ liệu cũ,
        # sống tới hết TTL (Task 11 review fix). Ghi thẳng đóng khe hở này ở
        # chiều ngược lại (ghi sau luôn thắng) — vẫn còn ca hiếm: một lần đọc
        # bắt đầu TRƯỚC lần ghi này nhưng hoàn tất SAU nó vẫn có thể ghi đè
        # cache bằng dữ liệu cũ hơn; chấp nhận được vì admin đổi giờ hiếm,
        # tiến trình chỉ có một worker.
        await cache_set(HOURS_CACHE_KEY, hours.model_dump(), HOURS_CACHE_TTL_SECONDS, encode=_encode_json)
        return hours

    async def _load_status_raw(self) -> dict:
        doc = await self.status.find_one(_SINGLETON) or {}
        return _status_doc_to_raw(doc)

    async def get_status(self) -> ShopStatusView:
        raw = await cache_get_or_load(
            STATUS_CACHE_KEY,
            STATUS_CACHE_TTL_SECONDS,
            self._load_status_raw,
            encode=_encode_json,
            decode=_decode_json,
        )
        return _status_raw_to_view(raw)

    async def set_busy(self, minutes: int) -> ShopStatusView:
        until = now_utc() + timedelta(minutes=minutes)
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": True, "busy_until": until, "updated_at": now_utc()}},
            upsert=True,
        )
        raw = {"is_busy": True, "busy_until": until.isoformat()}
        await self._write_through_status(raw)
        return _status_raw_to_view(raw)

    async def set_free(self) -> ShopStatusView:
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": False, "busy_until": None, "updated_at": now_utc()}},
            upsert=True,
        )
        raw = {"is_busy": False, "busy_until": None}
        await self._write_through_status(raw)
        return _status_raw_to_view(raw)

    @staticmethod
    async def _write_through_status(raw: dict) -> None:
        """Ghi thẳng dữ liệu THÔ vừa lưu vào cache thay vì `cache_delete` rồi
        để `get_status()` tự nạp lại (Task 11 review fix).

        Khe hở còn lại nếu dùng xoá: một `get_status()` đang chạy đọc TRƯỚC
        lần ghi Mongo này, hoàn tất SAU khi hàm này đã xoá cache — nó hồi sinh
        cache bằng dữ liệu cũ, sống tới hết TTL (30s) dù tiệm đã đổi trạng
        thái. Ghi thẳng khiến lần ghi SAU luôn thắng, đóng khe hở ở chiều hay
        gặp nhất. Ca hiếm còn sót: một lần đọc bắt đầu trước NHƯNG hoàn tất
        sau cả bước ghi thẳng này (chen đúng vào giữa hai dòng `update_one`
        và `cache_set` không giúp gì — vẫn còn khả năng race nếu có nhiều
        worker) vẫn có thể ghi đè bằng dữ liệu cũ hơn; chấp nhận được vì admin
        bấm bận/rảnh là thao tác hiếm, tiến trình chỉ chạy MỘT worker
        (AGENTS.md), và cache tự đúng lại sau tối đa 30s.
        """
        await cache_set(STATUS_CACHE_KEY, raw, STATUS_CACHE_TTL_SECONDS, encode=_encode_json)

    async def is_open_at(self, dt: datetime) -> bool:
        return is_within(await self.get_hours(), dt)


def is_within(hours: ShopHours, dt: datetime) -> bool:
    """Phép tính thuần, không chạm DB.

    Tách khỏi is_open_at để nơi cần kiểm tra nhiều mốc trong một ngày (tìm slot
    trống) lấy giờ mở cửa đúng MỘT lần thay vì mỗi mốc một truy vấn.
    """
    local = to_local(dt)

    # Python: Monday=0 ... Sunday=6. Quy ước của ta: Sunday=0 ... Saturday=6.
    weekday = (local.weekday() + 1) % 7
    if weekday in hours.closed_days:
        return False

    minutes = local.hour * 60 + local.minute
    return _to_minutes(hours.open_time) <= minutes < _to_minutes(hours.close_time)


def fits_before_closing(hours: ShopHours, start: datetime, duration_minutes: int) -> bool:
    """Cả ca làm phải nằm trong giờ mở cửa, không chỉ mốc bắt đầu.

    Chỉ xét mốc bắt đầu thì lịch 60 phút đặt lúc 18:45 vẫn lọt, bắt thợ làm đến
    19:45 trong khi tiệm đóng cửa 19:00 — và chính máy sẽ là bên gợi ý giờ đó.
    Mốc cuối cùng bị chiếm là start + duration - 1 slot.
    """
    last = start + timedelta(minutes=duration_minutes - 1)
    return is_within(hours, start) and is_within(hours, last)


def _to_minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)
