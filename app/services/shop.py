import math
from datetime import datetime, timedelta
from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.models.shop import ShopHours, ShopStatusView

_SINGLETON = {"_id": "singleton"}


class ShopService:
    """Giờ mở cửa và trạng thái bận/rảnh. Cả hai là document đơn lẻ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.hours = db["shop_hours"]
        self.status = db["shop_status"]

    async def get_hours(self) -> ShopHours:
        doc = await self.hours.find_one(_SINGLETON)
        return ShopHours(**{k: v for k, v in (doc or {}).items() if k != "_id"})

    async def set_hours(self, open_time: str, close_time: str, closed_days: List[int]) -> ShopHours:
        hours = ShopHours(open_time=open_time, close_time=close_time, closed_days=closed_days)
        await self.hours.update_one(_SINGLETON, {"$set": hours.model_dump()}, upsert=True)
        return hours

    async def get_status(self) -> ShopStatusView:
        """Quá busy_until thì coi như rảnh mà không cần admin bấm lại —
        đây là cách hiện thực yêu cầu 'hết giờ tự về Rảnh'."""
        doc = await self.status.find_one(_SINGLETON) or {}
        busy_until = doc.get("busy_until")
        if not doc.get("is_busy") or busy_until is None:
            return ShopStatusView(is_busy=False)

        if busy_until.tzinfo is None:
            busy_until = busy_until.replace(tzinfo=now_utc().tzinfo)

        remaining = (busy_until - now_utc()).total_seconds()
        if remaining <= 0:
            return ShopStatusView(is_busy=False)

        return ShopStatusView(
            is_busy=True,
            busy_until=busy_until,
            minutes_left=max(1, math.ceil(remaining / 60)),
        )

    async def set_busy(self, minutes: int) -> ShopStatusView:
        until = now_utc() + timedelta(minutes=minutes)
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": True, "busy_until": until, "updated_at": now_utc()}},
            upsert=True,
        )
        return await self.get_status()

    async def set_free(self) -> ShopStatusView:
        await self.status.update_one(
            _SINGLETON,
            {"$set": {"is_busy": False, "busy_until": None, "updated_at": now_utc()}},
            upsert=True,
        )
        return await self.get_status()

    async def is_open_at(self, dt: datetime) -> bool:
        hours = await self.get_hours()
        local = to_local(dt)

        # Python: Monday=0 ... Sunday=6. Quy ước của ta: Sunday=0 ... Saturday=6.
        weekday = (local.weekday() + 1) % 7
        if weekday in hours.closed_days:
            return False

        minutes = local.hour * 60 + local.minute
        return _to_minutes(hours.open_time) <= minutes < _to_minutes(hours.close_time)


def _to_minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)
