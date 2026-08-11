from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.errors import (ForbiddenError, NotFoundError,
                             OutsideShopHoursError, PastTimeError,
                             SlotTakenError)
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
