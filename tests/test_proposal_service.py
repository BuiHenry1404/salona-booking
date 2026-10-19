"""Vòng đời đề xuất (`proposals`) — nguồn sự thật duy nhất cho việc ghi lịch từ chat.

Checkpoint chỉ mang `proposal_id`; mọi quyết định ghi (chủ sở hữu, còn hạn, một
lần duy nhất, lịch đích chưa đổi) nằm ở `ProposalService.approve`. Không dùng
transaction (mongo:7 standalone): claim bằng `find_one_and_update`, index unique
partial của `slot_keys` là bước kiểm lại lúc ghi.
"""
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ, now_utc
from app.services import proposal as proposal_module
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import (APPROVING_STALE_SECONDS,
                                   PROPOSAL_MAX_AGE_MINUTES, ProposalService)

pytestmark = pytest.mark.asyncio


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def later():
    """Mốc của LƯỢT SAU — approve chỉ hợp lệ ở lượt sau lượt đề xuất."""
    return now_utc() + timedelta(seconds=5)


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


async def test_create_stores_a_pending_proposal_with_ten_minute_expiry(test_db):
    user = await a_user(test_db)
    p = await ProposalService(test_db).create(user, "book", tomorrow_at(15), "làm tóc")

    doc = await test_db["proposals"].find_one({"_id": p.id})
    assert doc["status"] == "pending" and doc["kind"] == "book"
    assert doc["user_id"] == str(user.id) and doc["note"] == "làm tóc"
    assert doc["unclear_count"] == 0
    assert doc["expires_at"] - doc["created_at"] == timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES)
    assert PROPOSAL_MAX_AGE_MINUTES == 10


async def test_proposals_have_the_lookup_index(test_db):
    info = await test_db["proposals"].index_information()
    assert any(v["key"] == [("user_id", 1), ("status", 1), ("expires_at", 1)]
               for v in info.values())


async def test_new_proposal_supersedes_the_previous_pending_one(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    first = await service.create(user, "book", tomorrow_at(15), None)
    second = await service.create(user, "book", tomorrow_at(16), None)

    assert (await service.get(user, str(first.id))).status == "superseded"
    assert (await service.get(user, str(second.id))).status == "pending"
    result = await service.approve(user, str(first.id), turn_at=later())
    assert result.outcome == "not_approvable" and result.reason == "superseded"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_another_customers_pending_is_not_superseded(test_db):
    lan = await a_user(test_db)
    hoa = await a_user(test_db, "0938111222", "Cô Hoa")
    service = ProposalService(test_db)
    mine = await service.create(lan, "book", tomorrow_at(15), None)
    await service.create(hoa, "book", tomorrow_at(16), None)
    assert (await service.get(lan, str(mine.id))).status == "pending"


async def test_approve_books_once_from_the_stored_values(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), "làm tóc")

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "approved" and result.kind == "book"
    assert result.appointment.start_at.astimezone(TZ) == tomorrow_at(15)
    assert result.appointment.note == "làm tóc"
    doc = await test_db["proposals"].find_one({"_id": p.id})
    assert doc["status"] == "approved"
    assert doc["result_appointment_id"] == str(result.appointment.id)
    assert doc["approved_at"] is not None


async def test_double_approve_gives_one_appointment_and_the_same_id(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)

    first = await service.approve(user, str(p.id), turn_at=later())
    second = await service.approve(user, str(p.id), turn_at=later())

    assert first.outcome == second.outcome == "approved"
    assert str(first.appointment.id) == str(second.appointment.id)
    assert await test_db["appointments"].count_documents({}) == 1


async def test_another_customers_proposal_id_is_rejected(test_db):
    lan = await a_user(test_db)
    hoa = await a_user(test_db, "0938111222", "Cô Hoa")
    service = ProposalService(test_db)
    p = await service.create(lan, "book", tomorrow_at(15), None)

    result = await service.approve(hoa, str(p.id), turn_at=later())

    assert result.outcome == "not_approvable" and result.reason == "not_found"
    assert await test_db["appointments"].count_documents({}) == 0
    assert (await service.get(lan, str(p.id))).status == "pending"
    assert await service.get(hoa, str(p.id)) is None


@pytest.mark.parametrize("bad_id", ["", "rác", "0" * 24])
async def test_unknown_or_garbage_id_is_not_approvable(test_db, bad_id):
    user = await a_user(test_db)
    result = await ProposalService(test_db).approve(user, bad_id, turn_at=later())
    assert result.outcome == "not_approvable" and result.reason == "not_found"


async def test_expired_proposal_is_not_approved(test_db, monkeypatch):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)

    future = now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES + 1)
    monkeypatch.setattr(proposal_module, "now_utc", lambda: future)
    result = await service.approve(user, str(p.id), turn_at=future)

    assert result.outcome == "not_approvable" and result.reason == "expired"
    assert await test_db["appointments"].count_documents({}) == 0
    assert (await test_db["proposals"].find_one({"_id": p.id}))["status"] == "expired"


async def test_approval_in_the_same_turn_as_the_proposal_is_refused(test_db):
    """Đồng ý phải tới ở LƯỢT SAU: mốc lượt duyệt phải sau lúc tạo đề xuất."""
    user = await a_user(test_db)
    turn_started = now_utc() - timedelta(seconds=1)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)

    result = await service.approve(user, str(p.id), turn_at=turn_started)

    assert result.outcome == "not_approvable" and result.reason == "same_turn"
    assert await test_db["appointments"].count_documents({}) == 0
    assert (await service.get(user, str(p.id))).status == "pending"


async def test_slot_taken_between_propose_and_approve_fails_without_a_write(test_db):
    user = await a_user(test_db)
    other = await a_user(test_db, "0938111222", "Cô Hoa")
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)
    await AppointmentService(test_db).create(other, tomorrow_at(15), None)

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "failed"
    assert "có người" in result.error.lower()
    assert await test_db["appointments"].count_documents({"user_id": str(user.id)}) == 0
    doc = await test_db["proposals"].find_one({"_id": p.id})
    assert doc["status"] == "failed" and doc["error"] == result.error
    # Thất bại cũng là kết thúc: duyệt lại không ghi gì.
    again = await service.approve(user, str(p.id), turn_at=later())
    assert again.outcome == "not_approvable" and again.reason == "failed"


async def test_move_reschedules_when_the_target_is_unchanged(test_db):
    user = await a_user(test_db, name="Chú Hùng")
    appts = AppointmentService(test_db)
    old = await appts.create(user, tomorrow_at(9), "làm tóc")
    service = ProposalService(test_db)
    p = await service.create(user, "move", tomorrow_at(10), None, target=old)

    doc = await test_db["proposals"].find_one({"_id": p.id})
    assert doc["appointment_id"] == str(old.id)
    assert doc["target_snapshot"]["status"] == "booked"

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "approved" and result.kind == "move"
    upcoming = await appts.upcoming_for(user)
    assert [a.start_at.astimezone(TZ) for a in upcoming] == [tomorrow_at(10)]
    assert upcoming[0].note == "làm tóc"


async def test_move_fails_when_the_target_was_cancelled_meanwhile(test_db):
    user = await a_user(test_db)
    appts = AppointmentService(test_db)
    old = await appts.create(user, tomorrow_at(9), None)
    service = ProposalService(test_db)
    p = await service.create(user, "move", tomorrow_at(10), None, target=old)
    await appts.cancel(user, str(old.id))

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "failed"
    assert await appts.upcoming_for(user) == []


async def test_cancel_fails_when_the_target_was_moved_meanwhile(test_db):
    """Dời qua REST/Telegram giữa hai lượt: lịch cũ đã thành `cancelled`, lịch mới
    là id khác — đề xuất hủy không được hủy nhầm cái gì."""
    user = await a_user(test_db)
    appts = AppointmentService(test_db)
    appt = await appts.create(user, tomorrow_at(9), None)
    service = ProposalService(test_db)
    p = await service.create(user, "cancel", appt.start_at, None, target=appt)
    moved = await appts.reschedule(user, str(appt.id), tomorrow_at(11), None)

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "failed"
    assert "không tìm thấy" in result.error.lower()
    assert [str(a.id) for a in await appts.upcoming_for(user)] == [str(moved.id)]


async def test_cancel_fails_when_the_target_start_changed_in_place(test_db):
    user = await a_user(test_db)
    appts = AppointmentService(test_db)
    appt = await appts.create(user, tomorrow_at(9), None)
    service = ProposalService(test_db)
    p = await service.create(user, "cancel", appt.start_at, None, target=appt)
    await test_db["appointments"].update_one(
        {"_id": appt.id}, {"$set": {"start_at": tomorrow_at(11)}})

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "failed"
    assert (await test_db["appointments"].find_one({"_id": appt.id}))["status"] == "booked"


async def test_cancel_approves_and_cancels(test_db):
    user = await a_user(test_db)
    appts = AppointmentService(test_db)
    appt = await appts.create(user, tomorrow_at(9), None)
    service = ProposalService(test_db)
    p = await service.create(user, "cancel", appt.start_at, None, target=appt)

    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "approved" and result.kind == "cancel"
    assert await appts.upcoming_for(user) == []
    again = await service.approve(user, str(p.id), turn_at=later())
    assert again.outcome == "approved" and str(again.appointment.id) == str(appt.id)


async def test_reject_then_approve_writes_nothing(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)

    assert await service.reject(user, str(p.id)) is True
    result = await service.approve(user, str(p.id), turn_at=later())

    assert result.outcome == "not_approvable" and result.reason == "rejected"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_supersede_marks_only_a_pending_proposal(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)
    assert await service.supersede(user, str(p.id)) is True
    assert await service.supersede(user, str(p.id)) is False
    assert (await service.get(user, str(p.id))).status == "superseded"


async def test_unclear_twice_rejects(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)

    first = await service.mark_unclear(user, str(p.id))
    assert first.status == "pending" and first.unclear_count == 1
    second = await service.mark_unclear(user, str(p.id))
    assert second.status == "rejected" and second.unclear_count == 2


async def test_a_stale_approving_claim_reads_as_failed(test_db):
    """Tiến trình chết giữa claim và ghi: `approving` treo quá 60 giây coi là
    `failed` lúc đọc — không ai duyệt lại được, không ghi thêm."""
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)
    await test_db["proposals"].update_one({"_id": p.id}, {"$set": {
        "status": "approving",
        "approving_at": now_utc() - timedelta(seconds=APPROVING_STALE_SECONDS + 1)}})

    assert (await service.get(user, str(p.id))).status == "failed"
    result = await service.approve(user, str(p.id), turn_at=later())
    assert result.outcome == "not_approvable" and result.reason == "failed"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_a_fresh_approving_claim_is_not_approved_twice(test_db):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)
    await test_db["proposals"].update_one({"_id": p.id}, {"$set": {
        "status": "approving", "approving_at": now_utc()}})

    result = await service.approve(user, str(p.id), turn_at=later())
    assert result.outcome == "not_approvable" and result.reason == "approving"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_create_requires_a_target_for_move_and_cancel(test_db):
    user = await a_user(test_db)
    with pytest.raises(ValueError):
        await ProposalService(test_db).create(user, "move", tomorrow_at(10), None)
    with pytest.raises(ValueError):
        await ProposalService(test_db).create(user, "cancel", tomorrow_at(10), None)


# --- fix round 1 (review M3) --------------------------------------------------

async def test_concurrent_approve_writes_once(test_db):
    """Năm lần duyệt chạy chồng: đúng một claim thắng, đúng một lịch. Lần nào báo
    approved (thắng, hoặc đọc sau khi đã xong — idempotent) cũng cùng một lịch."""
    import asyncio
    user = await a_user(test_db)
    p = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)

    results = await asyncio.gather(*[
        ProposalService(test_db).approve(user, str(p.id), turn_at=later()) for _ in range(5)])

    assert await test_db["appointments"].count_documents({}) == 1
    approved = [r for r in results if r.outcome == "approved"]
    assert approved and len({str(r.appointment.id) for r in approved}) == 1
    assert all(r.outcome == "approved" or r.reason == "approving" for r in results)


async def test_cancelling_the_caller_mid_approve_still_finishes_the_approval(test_db, monkeypatch):
    """Lượt chat bị huỷ giữa lúc duyệt (lượt mới thay): shield cho việc duyệt chạy
    xong — một lịch, đề xuất `approved` — thay vì kẹt `approving` rồi `failed`."""
    import asyncio

    from app.services import appointment as appointment_module
    user = await a_user(test_db)
    p = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)

    async def slow_notify(appt):
        await asyncio.sleep(0.6)
    monkeypatch.setattr(appointment_module.notifications, "appointment_created", slow_notify)

    task = asyncio.create_task(ProposalService(test_db).approve(user, str(p.id), turn_at=later()))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait(proposal_module._INFLIGHT_APPROVALS, timeout=3)

    doc = await test_db["proposals"].find_one({"_id": p.id})
    assert doc["status"] == "approved"
    assert await test_db["appointments"].count_documents({}) == 1
    again = await ProposalService(test_db).approve(user, str(p.id), turn_at=later())
    assert again.outcome == "approved" and str(again.appointment.id) == doc["result_appointment_id"]


async def test_a_lost_final_state_flip_is_logged(test_db, monkeypatch):
    user = await a_user(test_db)
    service = ProposalService(test_db)
    p = await service.create(user, "book", tomorrow_at(15), None)
    real = service.repo.transition

    async def racing(pid, from_status, to_status, *a, **kw):
        if to_status == "approved":
            await real(pid, "approving", "failed", {"error": "approving_stale"})
        return await real(pid, from_status, to_status, *a, **kw)

    events = []
    monkeypatch.setattr(service.repo, "transition", racing)
    monkeypatch.setattr(proposal_module.logger, "warning",
                        lambda event, **kw: events.append(event))

    result = await service.approve(user, str(p.id), turn_at=later())

    assert "proposal_state_race" in events
    assert result.outcome == "approved"                # lịch đã ghi thật
    assert await test_db["appointments"].count_documents({}) == 1
