from datetime import date, datetime, timedelta
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.clock import local_day_bounds, now_utc, to_local
from app.core.config import settings
from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError,
                             SlotTakenError)
from app.core.slots import SLOT_MINUTES, quantize, slot_keys_for
from app.models.appointment import Appointment, CreatedVia
from app.models.service import Service
from app.models.user import User
from app.repositories.appointment import AppointmentRepository
from app.services.notifications import notifications
from app.services.rate_limit import RateLimitService
from app.services.service_catalog import ServiceCatalogService
from app.services.shop import ShopService, fits_before_closing


class ServiceRef(BaseModel):
    """Dịch vụ tối giản để hiện tên (không giá, không thời lượng riêng) —
    REST, Socket.IO và Telegram đều ghép lịch với dịch vụ qua hình này."""

    id: str
    name: str


class AppointmentDetail(BaseModel):
    """Một lịch đã "dựng" đủ để hiện cho người xem: tên dịch vụ (không chỉ
    id) và giờ xong (`end_at`) — khách lớn tuổi cần MỐC giờ, không phải
    khoảng thời gian còn lại (CONTEXT.md, ràng buộc giao diện). REST router
    dùng `AppointmentService.describe` để dựng hình này thay vì tự tính lại
    (AGENTS.md: logic đặt lịch chỉ ở `app/services/`)."""

    id: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    note: Optional[str] = None
    status: Literal["booked", "cancelled"]
    user_name: Optional[str] = None
    phone: Optional[str] = None
    services: List[ServiceRef] = Field(default_factory=list)


async def describe_appointments(
    catalog: Optional[ServiceCatalogService], appointments: Sequence[Appointment]
) -> List[AppointmentDetail]:
    """Hàm dựng DUY NHẤT cho `AppointmentDetail`: REST
    (`AppointmentService.describe`), `SocketNotifier` và `TelegramNotifier`
    đều gọi đây — tên dịch vụ / giờ xong tính đúng MỘT chỗ (AGENTS.md: logic
    đặt lịch chỉ ở `app/services/`).

    `catalog=None`: notifier dựng không có `db` (test cũ, hoặc kênh chưa cấu
    hình) — bỏ qua tra tên, dịch vụ không khớp catalog thì vắng mặt trong
    `services` thay vì lỗi; `end_at` vẫn luôn đúng vì tính thẳng từ
    `duration_minutes`, không cần tra cứu.

    Tra tên MỘT LẦN cho cả danh sách (không N+1 query mỗi lịch)."""
    all_ids = sorted({sid for appt in appointments for sid in (appt.service_ids or [])})
    by_id: Dict[str, Service] = {}
    if all_ids and catalog is not None:
        by_id = {service.id: service for service in await catalog.get_many(all_ids)}

    details = []
    for appt in appointments:
        refs = [
            ServiceRef(id=sid, name=service.name)
            for sid in (appt.service_ids or [])
            if (service := by_id.get(sid)) is not None
        ]
        details.append(AppointmentDetail(
            id=str(appt.id),
            start_at=appt.start_at,
            end_at=appt.start_at + timedelta(minutes=appt.duration_minutes),
            duration_minutes=appt.duration_minutes,
            note=appt.note,
            status=appt.status,
            user_name=appt.user_name,
            phone=appt.phone,
            services=refs,
        ))
    return details


class AppointmentService:
    """Toàn bộ nghiệp vụ đặt lịch. Tool của agent, handler Telegram và REST cho
    React đều gọi lớp này — logic chỉ tồn tại một chỗ."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AppointmentRepository(db)
        self.shop = ShopService(db)
        self.rate_limit = RateLimitService(db)
        self.catalog = ServiceCatalogService(db)
        # Báo tin của các lệnh ghi chạy TRONG transaction (`session`): chưa
        # commit thì chưa được báo chủ tiệm — transaction có thể abort (lịch
        # biến mất) hoặc chạy lại callback (báo hai lần). Caller gửi sau commit
        # bằng `send_notices` (Task 3.6). Không có session thì báo ngay như cũ.
        self.deferred_notices: List[Tuple[str, Appointment]] = []

    async def _notify(self, method: str, appointment: Appointment, session) -> None:
        if session is None:
            await getattr(notifications, method)(appointment)
        else:
            self.deferred_notices.append((method, appointment))

    async def create(
        self,
        user: User,
        start_at: datetime,
        note: Optional[str],
        created_via: CreatedVia = "chat",
        *,
        service_ids: Optional[List[str]] = None,
        session=None,
    ) -> Appointment:
        """`session`: mọi đọc/ghi Mongo đi trong transaction của caller (chỉ
        `ProposalService.approve` dùng); None = hành vi cũ (REST, Telegram).

        `service_ids`: dịch vụ của lịch này — thời lượng là TỔNG thời lượng
        các dịch vụ (`ServiceCatalogService.duration_for`); không có dịch vụ
        nào (None hoặc rỗng) giữ hành vi cũ: `settings.booking_slot_minutes`.
        """
        start = quantize(start_at)
        duration = await self.catalog.duration_for(service_ids or [])
        # Giờ mở cửa đọc ngoài session (qua cache) — cấu hình, không phải dữ
        # liệu transaction cần cô lập.
        await self._validate_time(start, duration)

        user_id = str(user.id)

        # Idempotency: AI gọi tool hai lần cho cùng một yêu cầu thì lần thứ hai
        # trả về chính lịch đã tạo, thay vì báo trùng cho chính khách vừa đặt.
        existing = await self.repo.find_active_at(user_id, start, session=session)
        if existing:
            return existing

        # Hạn mức đặt SAU nhánh idempotency: lượt gọi lặp lại trả về lịch cũ
        # thì không được tính là một lần đặt mới.
        await self.rate_limit.check_and_hit(
            f"booking:user:{user_id}", settings.booking_max_per_hour, 3600, session=session
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
                service_ids=service_ids,
                session=session,
            )
        except SlotTakenError:
            # Index đã chặn xong, việc còn lại chỉ là nói cho đúng người. Lịch
            # 60 phút lúc 9:00 của chính khách làm 9:15 bị trùng: bảo "đã có
            # người đặt" thì khách tưởng người lạ giành mất giờ của mình.
            # CỐ Ý không truyền `session`: DuplicateKeyError đã làm Mongo abort
            # transaction, đọc trong session đó lỗi NoSuchTransaction (nhãn
            # TransientTransactionError -> `with_transaction` chạy lại mãi).
            # Lịch chiếm chỗ là dữ liệu ĐÃ commit nên đọc ngoài session thấy.
            clash = await self.repo.find_conflicting(slot_keys_for(start, duration))
            if clash and clash.user_id == user_id:
                raise SlotTakenError("Giờ này trùng với lịch bạn đã đặt rồi")
            raise
        await self._notify("appointment_created", appointment, session)
        return appointment

    async def _validate_time(self, start: datetime, duration: int) -> None:
        if start < now_utc():
            raise PastTimeError()
        if not fits_before_closing(await self.shop.get_hours(), start, duration):
            raise OutsideShopHoursError()

    async def own_booked(self, user: User, appointment_id: str, *, session=None) -> Appointment:
        """Lịch còn hiệu lực của chính khách (admin thì của ai cũng được)."""
        appt = await self.repo.get_by_id(appointment_id, session=session)
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
        *,
        service_ids: Optional[List[str]] = None,
        session=None,
    ) -> Appointment:
        """Dời một lịch sang giờ khác. Kết quả LUÔN là đúng một lịch.

        `service_ids`: `None` giữ nguyên dịch vụ (và thời lượng) của lịch cũ —
        khách không nhắc lại dịch vụ thì không có lý do đổi thời lượng. Khác
        `None` (kể cả `[]` tường minh) tính lại thời lượng từ danh sách MỚI
        (spec §6.2: danh sách này là TOÀN BỘ dịch vụ của lịch sau khi dời, không
        phải phần thêm vào).

        `session`: như `create` — trong transaction thì hai bước đặt/huỷ cùng
        commit hoặc cùng mất.

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
        old = await self.own_booked(user, appointment_id, session=session)
        start = quantize(new_start_at)
        if start == old.start_at:
            return old
        note = note or old.note   # khách không nhắc lại dịch vụ thì giữ dịch vụ cũ

        if service_ids is None:
            new_service_ids: Optional[List[str]] = old.service_ids or None
            duration = old.duration_minutes
        else:
            new_service_ids = service_ids
            duration = await self.catalog.duration_for(service_ids)

        overlaps_itself = set(slot_keys_for(start, duration)) & set(old.slot_keys)
        if overlaps_itself:
            await self._validate_time(start, duration)
            await self.rate_limit.check(
                f"booking:user:{old.user_id}", settings.booking_max_per_hour, 3600,
                session=session,
            )
            await self.repo.cancel(appointment_id, session=session)
            appointment = await self.create(user, start, note, created_via,
                                             service_ids=new_service_ids, session=session)
        else:
            appointment = await self.create(user, start, note, created_via,
                                             service_ids=new_service_ids, session=session)
            await self.repo.cancel(appointment_id, session=session)

        await self._notify("appointment_cancelled", old, session)
        return appointment

    async def cancel(self, user: User, appointment_id: str, *, session=None) -> None:
        appt = await self.repo.get_by_id(appointment_id, session=session)
        if not appt or appt.status != "booked":
            raise NotFoundError("Không tìm thấy lịch này")
        if appt.user_id != str(user.id) and user.role != "admin":
            raise ForbiddenError("Chỉ hủy được lịch của chính mình")

        # repo.cancel lọc status="booked" ngay trong câu update, nên nếu lịch bị
        # hủy xen vào giữa get_by_id và update_one thì không có gì đổi. Bỏ qua
        # giá trị trả về là báo "đã hủy" cho một thao tác chưa hủy được gì.
        if not await self.repo.cancel(appointment_id, session=session):
            raise NotFoundError("Không tìm thấy lịch này")
        await self._notify("appointment_cancelled", appt, session)

    async def upcoming_for(self, user: User) -> List[Appointment]:
        return await self.repo.upcoming_for_user(str(user.id), now=now_utc())

    async def describe(self, appointments: Sequence[Appointment]) -> List[AppointmentDetail]:
        """Dựng view đầy đủ cho một danh sách lịch — REST router dùng hàm
        này thay vì tự tính. Logic thật nằm ở `describe_appointments` (hàm
        module-level, dùng chung với `SocketNotifier`/`TelegramNotifier`)."""
        return await describe_appointments(self.catalog, appointments)

    async def day_schedule(self, day: date) -> List[Appointment]:
        start, end = local_day_bounds(day)
        return await self.repo.booked_between(start, end)

    async def own_covering(self, user: User, at: datetime, *,
                           ignore_appointment_id: Optional[str] = None) -> Optional[Appointment]:
        """Lịch còn hiệu lực của CHÍNH khách đang chiếm mốc `at` (bắt đầu ≤ `at`
        < bắt đầu + thời lượng) — để agent phân biệt "giờ này là lịch của anh
        chị" với "giờ này người khác đặt rồi". `ignore_appointment_id`: lịch
        đang được dời, coi như không có."""
        for appt in await self.day_schedule(to_local(at).date()):
            if appt.user_id != str(user.id) or str(appt.id) == ignore_appointment_id:
                continue
            if appt.start_at <= at < appt.start_at + timedelta(minutes=appt.duration_minutes):
                return appt
        return None

    async def find_free_slots(
        self, day: date, limit: int = 12, ignore_appointment_id: Optional[str] = None,
        *, duration_minutes: Optional[int] = None,
    ) -> List[datetime]:
        """Các mốc còn trống trong ngày: nằm trong giờ mở cửa, không trùng lịch
        đã có, và không ở quá khứ.

        `ignore_appointment_id`: coi lịch đó như không có — dùng khi khách đang
        DỜI chính lịch ấy, các mốc nó chiếm sẽ được nhả ra nên phải tính là trống.

        `duration_minutes`: thời lượng của lịch khách SẮP đặt (tổng thời lượng
        dịch vụ đã chọn) — mốc phải còn đủ chỗ cho NÓ, không phải cho lịch mặc
        định. `None` giữ hành vi cũ: `settings.booking_slot_minutes`.
        """
        start, end = local_day_bounds(day)
        taken: set = set()
        for appt in await self.repo.booked_between(start, end):
            if ignore_appointment_id and str(appt.id) == ignore_appointment_id:
                continue
            steps = appt.duration_minutes // SLOT_MINUTES
            for i in range(steps):
                taken.add(appt.start_at + timedelta(minutes=SLOT_MINUTES * i))
        duration = (duration_minutes if duration_minutes is not None
                   else settings.booking_slot_minutes)
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


async def send_notices(notices: Sequence[Tuple[str, Appointment]]) -> None:
    """Gửi các báo tin đã hoãn (`AppointmentService.deferred_notices`) — gọi
    SAU khi transaction đã commit, đúng một lần."""
    for method, appointment in notices:
        await getattr(notifications, method)(appointment)
