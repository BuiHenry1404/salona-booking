from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError,
                             RateLimitedError, SlotTakenError)
from app.core.clock import TZ
from app.models.user import User
from app.services.appointment import AppointmentService

pytestmark = pytest.mark.asyncio


def make_user(phone="0912345678", name="Cô Lan", role="user"):
    """Mỗi lần gọi sinh một ObjectId mới, nên hai user khác nhau thật sự khác id.

    Quan trọng: đừng truyền id=None — khi đó str(user.id) là "None" cho mọi user,
    khóa idempotency sẽ gộp nhầm lịch của hai người thành một.
    """
    return User(phone=phone, hashed_password="h", full_name=name, role=role)


@pytest.fixture
def future_day():
    return date(2099, 8, 7)


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


async def test_create_stores_the_appointment(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note="làm tóc")
    assert appt.status == "booked"
    assert appt.note == "làm tóc"
    assert appt.user_name == "Cô Lan"
    assert appt.phone == "0912345678"


async def test_create_rejects_a_time_in_the_past(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(PastTimeError):
        await svc.create(make_user(), datetime.now(timezone.utc) - timedelta(hours=1), note=None)


async def test_create_rejects_a_time_outside_shop_hours(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(OutsideShopHoursError):
        await svc.create(make_user(), future_local(3), note=None)


async def test_create_rounds_the_time_down_to_the_slot_grid(test_db):
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15, 7), note=None)
    assert appt.start_at.astimezone(TZ).minute == 0


async def test_calling_create_twice_with_same_args_returns_the_same_appointment(test_db):
    """Khóa idempotency: AI gọi tool hai lần không được sinh hai lịch."""
    svc = AppointmentService(test_db)
    user = make_user()
    first = await svc.create(user, future_local(15), note="làm tóc")
    second = await svc.create(user, future_local(15), note="làm tóc")
    assert first.id == second.id


async def test_another_user_at_the_same_time_is_rejected(test_db):
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(15), note=None)
    other = make_user(phone="0938111222", name="Cô Hoa")
    with pytest.raises(SlotTakenError):
        await svc.create(other, future_local(15), note=None)


async def test_cancel_by_the_owner_succeeds(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    await svc.cancel(user, str(appt.id))
    assert await svc.upcoming_for(user) == []


async def test_cancel_by_someone_else_is_forbidden(test_db):
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15), note=None)

    intruder = make_user(phone="0938111222", name="Người lạ")
    with pytest.raises(ForbiddenError):
        await svc.cancel(intruder, str(appt.id))


async def test_admin_may_cancel_anyone_s_appointment(test_db):
    svc = AppointmentService(test_db)
    owner = make_user()
    appt = await svc.create(owner, future_local(15), note=None)

    boss = make_user(phone="0901234567", name="Chủ tiệm", role="admin")
    await svc.cancel(boss, str(appt.id))
    assert await svc.upcoming_for(owner) == []


async def test_cancel_unknown_id_raises_not_found(test_db):
    svc = AppointmentService(test_db)
    with pytest.raises(NotFoundError):
        await svc.cancel(make_user(), "64b7f0c2e4b0a1a2b3c4d5e6")


async def test_free_slots_stay_inside_shop_hours(test_db, future_day):
    svc = AppointmentService(test_db)
    slots = await svc.find_free_slots(future_day)
    assert slots
    for slot in slots:
        localised = slot.astimezone(TZ)
        assert 8 <= localised.hour < 19


async def test_free_slots_exclude_booked_times(test_db, future_day):
    """A 60-min booking at 08:00 must black out 08:00, 08:15, 08:30 and 08:45.
    A later hour (09:00) must still be offered, and the list must not be empty."""
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(8), note=None)
    slots = await svc.find_free_slots(future_day)
    local_slots = [s.astimezone(TZ) for s in slots]
    # All four sub-slots of the booked hour must be absent
    assert future_local(8, 0) not in local_slots
    assert future_local(8, 15) not in local_slots
    assert future_local(8, 30) not in local_slots
    assert future_local(8, 45) not in local_slots
    # A genuinely free slot later in the day must be offered
    assert future_local(9, 0) in local_slots
    # The overall list must be non-empty
    assert slots


async def test_free_slots_for_a_past_day_are_empty(test_db):
    svc = AppointmentService(test_db)
    assert await svc.find_free_slots(date(2020, 1, 1)) == []


async def test_booking_cannot_run_past_closing_time(test_db):
    """Chỉ xét mốc bắt đầu là chưa đủ: lịch 60 phút lúc 18:45 kéo đến 19:45,
    trong khi tiệm đóng cửa 19:00."""
    svc = AppointmentService(test_db)
    with pytest.raises(OutsideShopHoursError):
        await svc.create(make_user(), future_local(18, 45), note=None)


async def test_free_slots_never_offer_a_slot_that_runs_past_closing(test_db, future_day):
    svc = AppointmentService(test_db)
    slots = await svc.find_free_slots(future_day, limit=100)
    assert slots, "phải còn chỗ trống trong ngày trống"
    duration = timedelta(minutes=60)
    close = datetime(2099, 8, 7, 19, 0, tzinfo=TZ)
    assert max(slots) + duration <= close


async def test_own_overlapping_booking_says_so_instead_of_blaming_a_stranger(test_db):
    """Khách có lịch 08:00 (60 phút) rồi xin thêm 08:15 phải được nói là trùng
    lịch của CHÍNH MÌNH, chứ không phải 'giờ đó đã có người đặt'."""
    svc = AppointmentService(test_db)
    user = make_user()
    await svc.create(user, future_local(9), note=None)
    with pytest.raises(SlotTakenError) as caught:
        await svc.create(user, future_local(9, 15), note=None)
    assert "chính" in caught.value.message.lower() or "bạn" in caught.value.message.lower()


async def test_datetimes_read_back_from_mongo_keep_their_timezone(test_db):
    """Motor mặc định trả datetime naive; mất tzinfo là trình duyệt hiện lệch 7 tiếng."""
    svc = AppointmentService(test_db)
    user = make_user()
    await svc.create(user, future_local(15), note=None)
    [read_back] = await svc.upcoming_for(user)
    assert read_back.start_at.tzinfo is not None
    assert read_back.start_at == future_local(15)


async def test_cancelling_twice_reports_not_found_the_second_time(test_db):
    """repo.cancel lọc status="booked" trong câu update, nên lần hủy thứ hai
    không đổi gì. Bỏ qua giá trị trả về là báo thành công cho việc chưa làm."""
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note=None)
    await svc.cancel(user, str(appt.id))
    with pytest.raises(NotFoundError):
        await svc.cancel(user, str(appt.id))


async def test_created_at_is_timezone_aware(test_db):
    """BaseRepository.create dùng now_utc(), không phải datetime.utcnow() naive."""
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(16), note=None)
    assert appt.created_at.tzinfo is not None


def _nth_free_slot(i: int) -> datetime:
    """Mốc thứ i không chồng lịch nào khác.

    Mỗi lịch dài 60 phút và tiệm mở 08:00-19:00, nên một ngày chỉ chứa được 11
    lịch liền nhau — 20 lịch phải trải sang ngày thứ hai.
    """
    return future_local(8 + i % 10) + timedelta(days=i // 10)


async def test_booking_quota_blocks_the_twenty_first_in_an_hour(test_db):
    svc = AppointmentService(test_db)
    user = make_user()
    for i in range(20):
        await svc.create(user, _nth_free_slot(i), note=None)

    with pytest.raises(RateLimitedError):
        await svc.create(user, _nth_free_slot(20), note=None)


async def test_booking_quota_is_per_user(test_db):
    svc = AppointmentService(test_db)
    heavy, light = make_user(), make_user(phone="0987654321", name="Cô Hoa")
    for i in range(20):
        await svc.create(heavy, _nth_free_slot(i), note=None)

    appt = await svc.create(light, _nth_free_slot(20), note=None)
    assert appt.status == "booked"


async def test_a_repeated_identical_booking_does_not_consume_the_quota(test_db):
    """Nhánh idempotency trả về lịch cũ, nên không được tính là lần đặt mới."""
    svc = AppointmentService(test_db)
    user = make_user()
    for _ in range(30):
        await svc.create(user, _nth_free_slot(0), note=None)

    appt = await svc.create(user, _nth_free_slot(1), note=None)
    assert appt.status == "booked"


class TestReschedule:
    """BUG-1 (CONTEXT.md, checkpoint 2026-09-14): "dời lịch" từng thành ĐẶT THÊM
    lịch — `create` chỉ chống trùng cùng một mốc giờ, hai mốc khác nhau là hai
    lịch. Hệ thống không có luồng đổi lịch; giờ có, và nó phải để lại ĐÚNG MỘT
    lịch dù thành hay bại.
    """

    async def _booked(self, db, user, start):
        return await AppointmentService(db).create(user, start, "làm tóc")

    async def test_moving_leaves_exactly_one_appointment(self, test_db):
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))

        moved = await svc.reschedule(user, str(old.id), future_local(10), None)

        upcoming = await svc.upcoming_for(user)
        assert [a.start_at for a in upcoming] == [future_local(10)]
        assert moved.id != old.id
        assert (await svc.repo.get_by_id(str(old.id))).status == "cancelled"

    async def test_the_note_carries_over_when_the_customer_gives_none(self, test_db):
        """Khách nói "chuyển qua 10 giờ" không nhắc lại dịch vụ — dịch vụ vẫn là
        cái đã đặt, không được rơi mất."""
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))

        moved = await svc.reschedule(user, str(old.id), future_local(10), None)
        assert moved.note == "làm tóc"

    async def test_a_new_note_replaces_the_old_one(self, test_db):
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))

        moved = await svc.reschedule(user, str(old.id), future_local(10), "làm nail")
        assert moved.note == "làm nail"

    async def test_moving_by_fifteen_minutes_over_its_own_slot_works(self, test_db):
        """Lịch 60 phút lúc 9:00 dời sang 9:15 chồng lên chính nó. Với `create`
        thuần thì index báo trùng — reschedule phải nhả lịch cũ trước."""
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))

        await svc.reschedule(user, str(old.id), future_local(9, 15), None)

        upcoming = await svc.upcoming_for(user)
        assert [a.start_at for a in upcoming] == [future_local(9, 15)]

    async def test_when_the_new_time_is_taken_the_old_appointment_survives(self, test_db):
        """Thất bại thì khách vẫn còn lịch cũ — mất lịch tệ hơn không dời được."""
        svc = AppointmentService(test_db)
        user, other = make_user(), make_user(phone="0938111222", name="Cô Hoa")
        old = await self._booked(test_db, user, future_local(9))
        await self._booked(test_db, other, future_local(10))

        with pytest.raises(SlotTakenError):
            await svc.reschedule(user, str(old.id), future_local(10), None)

        upcoming = await svc.upcoming_for(user)
        assert [a.start_at for a in upcoming] == [future_local(9)]
        assert (await svc.repo.get_by_id(str(old.id))).status == "booked"

    async def test_moving_to_the_same_time_changes_nothing(self, test_db):
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))

        same = await svc.reschedule(user, str(old.id), future_local(9), None)

        assert same.id == old.id
        assert len(await svc.upcoming_for(user)) == 1

    async def test_cannot_move_someone_elses_appointment(self, test_db):
        svc = AppointmentService(test_db)
        owner, intruder = make_user(), make_user(phone="0938111222", name="Người lạ")
        old = await self._booked(test_db, owner, future_local(9))

        with pytest.raises(ForbiddenError):
            await svc.reschedule(intruder, str(old.id), future_local(10), None)
        assert (await svc.repo.get_by_id(str(old.id))).status == "booked"

    async def test_unknown_or_cancelled_id_is_not_found(self, test_db):
        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))
        await svc.cancel(user, str(old.id))

        with pytest.raises(NotFoundError):
            await svc.reschedule(user, str(old.id), future_local(10), None)
        with pytest.raises(NotFoundError):
            await svc.reschedule(user, "không phải id", future_local(10), None)

    async def test_owner_sees_both_a_cancellation_and_a_new_booking(self, test_db):
        """Chủ tiệm nhận tin qua Telegram/Socket. Dời lịch phải báo cả hai vế,
        không thì trên điện thoại chủ tiệm lịch 9 giờ vẫn còn đó."""
        from app.services.notifications import notifications

        class Spy:
            created: list = []
            cancelled: list = []

            async def appointment_created(self, a): self.created.append(a.start_at)
            async def appointment_cancelled(self, a): self.cancelled.append(a.start_at)
            async def shop_status_changed(self, s): ...

        svc = AppointmentService(test_db)
        user = make_user()
        old = await self._booked(test_db, user, future_local(9))
        spy = Spy()
        notifications.register(spy)
        try:
            await svc.reschedule(user, str(old.id), future_local(10), None)
        finally:
            notifications.clear()

        assert spy.created == [future_local(10)]
        assert spy.cancelled == [future_local(9)]
