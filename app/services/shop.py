import json
import math
from datetime import datetime, timedelta
from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.infrastructure.redis import cache_delete, cache_get_or_load
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
        await cache_delete(HOURS_CACHE_KEY)
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
        await cache_delete(STATUS_CACHE_KEY)
        return await self.get_status()

    async def set_free(self) -> ShopStatusView:
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": False, "busy_until": None, "updated_at": now_utc()}},
            upsert=True,
        )
        await cache_delete(STATUS_CACHE_KEY)
        return await self.get_status()

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
