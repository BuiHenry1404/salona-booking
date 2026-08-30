from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import month_window_bounds, now_utc, recent_month_keys
from app.repositories.appointment import AppointmentRepository

MONTHS_IN_WINDOW = 12


class StatsService:
    """Số liệu tổng hợp cho bảng điều khiển chủ tiệm."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.appointments = AppointmentRepository(db)

    async def customers_by_month(self, months: int = MONTHS_IN_WINDOW) -> List[dict]:
        """Số khách duy nhất có lịch mỗi tháng, đúng `months` tháng gần nhất.

        Khung tháng do lịch VIỆT NAM quyết định chứ không do dữ liệu: tiệm mới
        mở hoặc tháng ế vẫn phải hiện thành cột 0. Trả mảng ngắn đi thì biểu đồ
        bên React lệch trục, chủ tiệm nhìn tưởng tháng đó biến mất.
        """
        now = now_utc()
        start, end = month_window_bounds(months, now)
        counts = await self.appointments.distinct_customers_by_month(start, end)
        return [
            {"month": key, "customer_count": counts.get(key, 0)}
            for key in recent_month_keys(months, now)
        ]
