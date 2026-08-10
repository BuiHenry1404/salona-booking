from datetime import date, datetime, timedelta
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import local_day_bounds, now_utc
from app.core.config import settings
from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError)
from app.core.slots import SLOT_MINUTES, quantize
from app.models.appointment import Appointment, CreatedVia
from app.models.user import User
from app.repositories.appointment import AppointmentRepository
from app.services.shop import ShopService

class AppointmentService:
    """Toàn bộ nghiệp vụ đặt lịch. Tool của agent, handler Telegram và REST cho
    React đều gọi lớp này — logic chỉ tồn tại một chỗ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AppointmentRepository(db)
        self.shop = ShopService(db)

    async def create(
        self,
        user: User,
        start_at: datetime,
        note: Optional[str],
        created_via: CreatedVia = "chat",
    ) -> Appointment:
        start = quantize(start_at)
        now = now_utc()

        if start < now:
            raise PastTimeError()
        if not await self.shop.is_open_at(start):
            raise OutsideShopHoursError()

        user_id = str(user.id)

        # Idempotency: AI gọi tool hai lần cho cùng một yêu cầu thì lần thứ hai
        # trả về chính lịch đã tạo, thay vì báo trùng cho chính khách vừa đặt.
        existing = await self.repo.find_active_at(user_id, start)
        if existing:
            return existing

        return await self.repo.insert_booked(
            user_id=user_id,
            user_name=user.full_name,
            phone=user.phone,
            start_at=start,
            duration_minutes=settings.booking_slot_minutes,
            note=note,
            created_via=created_via,
        )

    async def cancel(self, user: User, appointment_id: str) -> None:
        appt = await self.repo.get_by_id(appointment_id)
        if not appt or appt.status != "booked":
            raise NotFoundError("Không tìm thấy lịch này")
        if appt.user_id != str(user.id) and user.role != "admin":
            raise ForbiddenError("Chỉ hủy được lịch của chính mình")
        await self.repo.cancel(appointment_id)

    async def upcoming_for(self, user: User) -> List[Appointment]:
        return await self.repo.upcoming_for_user(str(user.id), now=now_utc())

    async def day_schedule(self, day: date) -> List[Appointment]:
        start, end = local_day_bounds(day)
        return await self.repo.booked_between(start, end)

    async def find_free_slots(self, day: date, limit: int = 12) -> List[datetime]:
        """Các mốc còn trống trong ngày: nằm trong giờ mở cửa, không trùng lịch
        đã có, và không ở quá khứ."""
        start, end = local_day_bounds(day)
        tz = start.tzinfo
        taken: set = set()
        for appt in await self.repo.booked_between(start, end):
            appt_start = appt.start_at.replace(tzinfo=tz)
            steps = appt.duration_minutes // SLOT_MINUTES
            for i in range(steps):
                taken.add(appt_start + timedelta(minutes=SLOT_MINUTES * i))
        duration = settings.booking_slot_minutes
        now = now_utc()

        free: List[datetime] = []
        cursor = start
        while cursor < end and len(free) < limit:
            if cursor >= now and cursor not in taken and await self.shop.is_open_at(cursor):
                if not self._overlaps(cursor, duration, taken):
                    free.append(cursor)
            cursor += timedelta(minutes=SLOT_MINUTES)
        return free

    @staticmethod
    def _overlaps(candidate: datetime, duration: int, taken: set) -> bool:
        steps = duration // SLOT_MINUTES
        return any(
            candidate + timedelta(minutes=SLOT_MINUTES * i) in taken for i in range(steps)
        )
