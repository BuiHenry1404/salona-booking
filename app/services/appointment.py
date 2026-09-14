from datetime import date, datetime, timedelta
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import local_day_bounds, now_utc
from app.core.config import settings
from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError,
                             SlotTakenError)
from app.core.slots import SLOT_MINUTES, quantize, slot_keys_for
from app.models.appointment import Appointment, CreatedVia
from app.models.user import User
from app.repositories.appointment import AppointmentRepository
from app.services.notifications import notifications
from app.services.rate_limit import RateLimitService
from app.services.shop import ShopService, fits_before_closing

class AppointmentService:
    """Toàn bộ nghiệp vụ đặt lịch. Tool của agent, handler Telegram và REST cho
    React đều gọi lớp này — logic chỉ tồn tại một chỗ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AppointmentRepository(db)
        self.shop = ShopService(db)
        self.rate_limit = RateLimitService(db)

    async def create(
        self,
        user: User,
        start_at: datetime,
        note: Optional[str],
        created_via: CreatedVia = "chat",
    ) -> Appointment:
        start = quantize(start_at)
        duration = settings.booking_slot_minutes
        await self._validate_time(start, duration)

        user_id = str(user.id)

        # Idempotency: AI gọi tool hai lần cho cùng một yêu cầu thì lần thứ hai
        # trả về chính lịch đã tạo, thay vì báo trùng cho chính khách vừa đặt.
        existing = await self.repo.find_active_at(user_id, start)
        if existing:
            return existing

        # Hạn mức đặt SAU nhánh idempotency: lượt gọi lặp lại trả về lịch cũ
        # thì không được tính là một lần đặt mới.
        await self.rate_limit.check_and_hit(
            f"booking:user:{user_id}", settings.booking_max_per_hour, 3600
        )

        try:
            appointment = await self.repo.insert_booked(
                user_id=user_id,
                user_name=user.full_name,
                phone=user.phone,
                start_at=start,
                duration_minutes=duration,
                note=note,
                created_via=created_via,
            )
        except SlotTakenError:
            # Index đã chặn xong, việc còn lại chỉ là nói cho đúng người. Lịch
            # 60 phút lúc 9:00 của chính khách làm 9:15 bị trùng: bảo "đã có
            # người đặt" thì khách tưởng người lạ giành mất giờ của mình.
            clash = await self.repo.find_conflicting(slot_keys_for(start, duration))
            if clash and clash.user_id == user_id:
                raise SlotTakenError("Giờ này trùng với lịch bạn đã đặt rồi")
            raise
        await notifications.appointment_created(appointment)
        return appointment

    async def _validate_time(self, start: datetime, duration: int) -> None:
        if start < now_utc():
            raise PastTimeError()
        if not fits_before_closing(await self.shop.get_hours(), start, duration):
            raise OutsideShopHoursError()

    async def own_booked(self, user: User, appointment_id: str) -> Appointment:
        """Lịch còn hiệu lực của chính khách (admin thì của ai cũng được)."""
        appt = await self.repo.get_by_id(appointment_id)
        if not appt or appt.status != "booked":
            raise NotFoundError("Không tìm thấy lịch này")
        if appt.user_id != str(user.id) and user.role != "admin":
            raise ForbiddenError("Chỉ hủy hay dời được lịch của chính mình")
        return appt

    async def reschedule(
        self,
        user: User,
        appointment_id: str,
        new_start_at: datetime,
        note: Optional[str],
        created_via: CreatedVia = "chat",
    ) -> Appointment:
        """Dời một lịch sang giờ khác. Kết quả LUÔN là đúng một lịch.

        Trước đây không có luồng này: "chuyển giùm anh qua 10 giờ" thành
        `create` ở 10 giờ, lịch 9 giờ vẫn nằm đó — hai lịch, bot vẫn "Xong rồi
        ạ" (CONTEXT.md, BUG-1). `create` chỉ chống trùng CÙNG một mốc giờ, nên
        không có gì chặn được.

        Thứ tự cố ý: ĐẶT MỚI TRƯỚC, HỦY CŨ SAU. Đặt mới có thể hỏng vì nhiều
        lẽ (giờ vừa bị người khác lấy, quá hạn mức), hủy cũ thì gần như không.
        Hủy trước rồi đặt hụt là khách mất lịch — tệ hơn không dời được.

        Ngoại lệ duy nhất: giờ mới chồng lên chính lịch cũ (9:00 → 9:15 với
        lịch 60 phút). Index sẽ báo trùng với chính nó, nên phải nhả lịch cũ
        trước. Kiểm hết mọi thứ có thể kiểm (quá khứ, giờ mở cửa, hạn mức)
        TRƯỚC khi nhả — sau đó chỉ còn race thuần, và không ai khác giữ được
        các mốc đó vì chúng vừa mới thuộc về chính khách.
        """
        old = await self.own_booked(user, appointment_id)
        start = quantize(new_start_at)
        if start == old.start_at:
            return old
        note = note or old.note   # khách không nhắc lại dịch vụ thì giữ dịch vụ cũ

        duration = settings.booking_slot_minutes
        overlaps_itself = set(slot_keys_for(start, duration)) & set(old.slot_keys)
        if overlaps_itself:
            await self._validate_time(start, duration)
            await self.rate_limit.check(
                f"booking:user:{old.user_id}", settings.booking_max_per_hour, 3600
            )
            await self.repo.cancel(appointment_id)
            appointment = await self.create(user, start, note, created_via)
        else:
            appointment = await self.create(user, start, note, created_via)
            await self.repo.cancel(appointment_id)

        await notifications.appointment_cancelled(old)
        return appointment

    async def cancel(self, user: User, appointment_id: str) -> None:
        appt = await self.repo.get_by_id(appointment_id)
        if not appt or appt.status != "booked":
            raise NotFoundError("Không tìm thấy lịch này")
        if appt.user_id != str(user.id) and user.role != "admin":
            raise ForbiddenError("Chỉ hủy được lịch của chính mình")

        # repo.cancel lọc status="booked" ngay trong câu update, nên nếu lịch bị
        # hủy xen vào giữa get_by_id và update_one thì không có gì đổi. Bỏ qua
        # giá trị trả về là báo "đã hủy" cho một thao tác chưa hủy được gì.
        if not await self.repo.cancel(appointment_id):
            raise NotFoundError("Không tìm thấy lịch này")
        await notifications.appointment_cancelled(appt)

    async def upcoming_for(self, user: User) -> List[Appointment]:
        return await self.repo.upcoming_for_user(str(user.id), now=now_utc())

    async def day_schedule(self, day: date) -> List[Appointment]:
        start, end = local_day_bounds(day)
        return await self.repo.booked_between(start, end)

    async def find_free_slots(
        self, day: date, limit: int = 12, ignore_appointment_id: Optional[str] = None
    ) -> List[datetime]:
        """Các mốc còn trống trong ngày: nằm trong giờ mở cửa, không trùng lịch
        đã có, và không ở quá khứ.

        `ignore_appointment_id`: coi lịch đó như không có — dùng khi khách đang
        DỜI chính lịch ấy, các mốc nó chiếm sẽ được nhả ra nên phải tính là trống.
        """
        start, end = local_day_bounds(day)
        taken: set = set()
        for appt in await self.repo.booked_between(start, end):
            if ignore_appointment_id and str(appt.id) == ignore_appointment_id:
                continue
            steps = appt.duration_minutes // SLOT_MINUTES
            for i in range(steps):
                taken.add(appt.start_at + timedelta(minutes=SLOT_MINUTES * i))
        duration = settings.booking_slot_minutes
        now = now_utc()
        hours = await self.shop.get_hours()  # lấy một lần, không hỏi lại mỗi mốc

        free: List[datetime] = []
        cursor = start
        while cursor < end and len(free) < limit:
            if cursor >= now and fits_before_closing(hours, cursor, duration):
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
