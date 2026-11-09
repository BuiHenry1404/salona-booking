"""Task 2: thời lượng lịch tính theo tổng thời lượng các dịch vụ được chọn,
không còn cố định `settings.booking_slot_minutes` khi khách có chọn dịch vụ.

`AppointmentService.create`/`reschedule`, `AppointmentService.find_free_slots`
và `ProposalService.create` nhận thêm `service_ids` (keyword-only, mặc định
giữ hành vi cũ). Dữ liệu dịch vụ mock seed qua `ServiceCatalogService.upsert_many`
(Task 1): `toc-nhuom` 120', `nail-son-gel` 45'.
"""
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ, now_utc
from app.core.errors import SlotTakenError
from app.core.slots import slot_keys_for
from app.models.service import Service
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import ProposalService
from app.services.service_catalog import ServiceCatalogService

pytestmark = pytest.mark.asyncio


def make_user(phone="0912345678", name="Cô Lan"):
    return User(phone=phone, hashed_password="h", full_name=name, role="user")


def future_local(h, mi=0):
    return datetime(2099, 8, 7, h, mi, tzinfo=TZ)


def future_day():
    from datetime import date
    return date(2099, 8, 7)


async def seed_services(db):
    catalog = ServiceCatalogService(db)
    await catalog.upsert_many([
        Service(id="toc-nhuom", category="toc", name="Nhuộm", price_min=350000,
                price_max=700000, duration_minutes=120),
        Service(id="nail-son-gel", category="nail", name="Sơn gel", price_min=120000,
                price_max=180000, duration_minutes=45),
        Service(id="toc-goi-say", category="toc", name="Gội sấy", price_min=50000,
                price_max=70000, duration_minutes=30),
    ])


def later():
    """Mốc của LƯỢT SAU — approve chỉ hợp lệ ở lượt sau lượt đề xuất."""
    return now_utc() + timedelta(seconds=5)


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


async def test_dye_booking_takes_eight_slot_keys(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15), note="nhuộm tóc",
                            service_ids=["toc-nhuom"])
    assert appt.duration_minutes == 120
    assert appt.service_ids == ["toc-nhuom"]
    assert len(appt.slot_keys) == 8
    assert appt.slot_keys == slot_keys_for(appt.start_at, 120)


async def test_dye_blocks_a_nail_customer_inside_its_window(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    await svc.create(make_user(), future_local(15), note="nhuộm tóc",
                     service_ids=["toc-nhuom"])
    other = make_user(phone="0938111222", name="Cô Hoa")
    with pytest.raises(SlotTakenError):
        await svc.create(other, future_local(15, 30), note=None)


async def test_no_service_uses_default_duration(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    appt = await svc.create(make_user(), future_local(15), note=None)
    from app.core.config import settings
    assert appt.duration_minutes == settings.booking_slot_minutes
    assert appt.service_ids == []


async def test_move_keeps_old_duration_without_service_ids(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note="nhuộm tóc",
                            service_ids=["toc-nhuom"])
    moved = await svc.reschedule(user, str(appt.id), future_local(16), note=None)
    assert moved.duration_minutes == 120
    assert moved.service_ids == ["toc-nhuom"]


async def test_move_with_service_ids_recomputes_duration(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    user = make_user()
    appt = await svc.create(user, future_local(15), note="sơn gel",
                            service_ids=["nail-son-gel"])
    assert appt.duration_minutes == 45
    moved = await svc.reschedule(user, str(appt.id), future_local(16), note=None,
                                 service_ids=["nail-son-gel", "toc-nhuom"])
    assert moved.duration_minutes == 165
    assert moved.service_ids == ["nail-son-gel", "toc-nhuom"]


async def test_find_free_slots_respects_duration(test_db):
    await seed_services(test_db)
    svc = AppointmentService(test_db)
    free = await svc.find_free_slots(future_day(), limit=200, duration_minutes=120)
    assert future_local(17, 15) not in free
    assert future_local(17, 0) in free


async def test_proposal_create_computes_duration_from_services(test_db):
    await seed_services(test_db)
    user = await a_user(test_db)
    proposals = ProposalService(test_db)
    p = await proposals.create(user, "book", future_local(15), "nhuộm tóc",
                               service_ids=["toc-nhuom"])
    assert p.duration_minutes == 120
    assert p.service_ids == ["toc-nhuom"]


async def test_approving_a_service_proposal_books_its_duration(test_db):
    await seed_services(test_db)
    user = await a_user(test_db)
    proposals = ProposalService(test_db)
    p = await proposals.create(user, "book", future_local(15), "nhuộm tóc",
                               service_ids=["toc-nhuom"])
    result = await proposals.approve(user, str(p.id), turn_at=later())
    assert result.outcome == "approved"
    assert result.appointment.duration_minutes == 120
    assert result.appointment.service_ids == ["toc-nhuom"]
