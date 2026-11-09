"""TDD cho `app/agents/salon/orchestrator/decide.py` (Task 8) — `veto()` (hạ
cấp nhãn, không model) và `apply_decision()` (cổng ghi duy nhất, một
transaction cho ghi lịch + quyết định ledger)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from app.agents.salon.orchestrator.decide import Veto, apply_decision, veto
from app.agents.salon.shared.schemas import Moment, PendingView
from app.agents.salon.turn.ledger import TurnLedger
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.services.auth import AuthService
from app.services.proposal import PROPOSAL_MAX_AGE_MINUTES, ProposalService


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


def tomorrow_at(hour, minute=0):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _moment(dt: datetime) -> Moment:
    return Moment(iso=to_local(dt).isoformat(), spoken=format_vi_datetime(dt))


def _pending_view(proposal) -> PendingView:
    return PendingView(
        id=str(proposal.id),
        kind=proposal.kind,
        at=_moment(proposal.start_at),
        expires_at=proposal.expires_at,
        target_id=proposal.appointment_id,
        service_ids=list(proposal.service_ids),
        unclear_count=proposal.unclear_count,
    )


async def _turn(db, *, asked_at=None):
    """Mở một lượt ledger cho `apply_decision` ghi quyết định vào."""
    ledger = TurnLedger(db)
    turn_id = str(uuid.uuid4())
    asked_at = asked_at or now_utc()
    await ledger.start(
        turn_id=turn_id, user_id="unused", thread_id="unused", asked_at=asked_at, kind="typed",
    )
    return ledger, turn_id, asked_at


def later(asked_at=None):
    """Mốc của LƯỢT SAU (approve chỉ hợp lệ ở lượt sau lượt đề xuất)."""
    base = asked_at or now_utc()
    return base + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# veto()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, question, protected_hours, expected_label, expected_extra",
    [
        # approve + giờ khác đề xuất (9h) -> change
        ("approve", "thôi đổi qua 3 giờ chiều đi", (), "change", {"reason": "other_time"}),
        # approve + giờ nêu lại trùng một mốc trong protected -> giữ approve
        ("approve", "ừ 2 giờ chiều đi", (14,), "approve", {}),
        # change + nhắc lại ĐÚNG giờ đề xuất (9h) -> từ chối same_time_as_pending
        ("change", "ừ đúng rồi 9 giờ sáng đó", (), "change", {"reject_code": "same_time_as_pending"}),
        # reject -> giữ nguyên (không đụng tới)
        ("reject", "thôi khỏi", (), "reject", {}),
    ],
)
def test_veto_table(label, question, protected_hours, expected_label, expected_extra):
    now = datetime(2026, 11, 10, 8, 0, tzinfo=TZ)
    pending_at = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
    pending = PendingView(
        id="p1", kind="book", at=_moment(pending_at),
        expires_at=now + timedelta(minutes=10), target_id=None, service_ids=[], unclear_count=0,
    )
    protected = [pending_at.replace(hour=h) for h in protected_hours]

    result = veto(label, None, question, pending, now, protected=protected)

    assert result.label == expected_label
    for key, value in expected_extra.items():
        assert getattr(result, key) == value


def test_veto_approve_with_relative_free_aside_is_unclear():
    now = datetime(2026, 11, 10, 8, 0, tzinfo=TZ)
    pending_at = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
    pending = PendingView(
        id="p1", kind="book", at=_moment(pending_at),
        expires_at=now + timedelta(minutes=10), target_id=None, service_ids=[], unclear_count=0,
    )

    result = veto("approve", "relative_free", "chiều nay rảnh thì đặt luôn", pending, now)

    assert result == Veto(label="unclear", reason="relative_free")


@pytest.mark.parametrize("aside", ["defer_only", "defer_with_request"])
def test_veto_approve_with_defer_aside_is_none(aside):
    now = datetime(2026, 11, 10, 8, 0, tzinfo=TZ)
    pending_at = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
    pending = PendingView(
        id="p1", kind="book", at=_moment(pending_at),
        expires_at=now + timedelta(minutes=10), target_id=None, service_ids=[], unclear_count=0,
    )

    result = veto("approve", aside, "để sau tính", pending, now)

    assert result == Veto(label="none", reason="defer")


# ---------------------------------------------------------------------------
# apply_decision()
# ---------------------------------------------------------------------------


async def test_approve_books_and_records_in_one_transaction(test_db):
    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), "làm tóc")
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="approve", turn_id=turn_id,
        asked_at=later(asked_at), ledger=ledger,
    )

    assert outcome.outcome == "approved"
    assert outcome.appointment_id is not None
    assert await test_db["appointments"].count_documents({"status": "booked"}) == 1
    record = await ledger.record(turn_id)
    assert record.decision is not None and record.decision.outcome == "approved"


async def test_approve_on_taken_slot_is_failed_with_nearest(test_db):
    taker = await a_user(test_db, "0938111222", "Cô Hoa")
    from app.services.appointment import AppointmentService
    await AppointmentService(test_db).create(taker, tomorrow_at(15), None)

    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), "làm tóc")
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="approve", turn_id=turn_id,
        asked_at=later(asked_at), ledger=ledger,
    )

    assert outcome.outcome == "failed"
    assert outcome.error is not None
    assert 1 <= len(outcome.nearest) <= 3
    assert (await test_db["proposals"].find_one({"_id": proposal.id}))["status"] == "failed"
    record = await ledger.record(turn_id)
    assert record.decision.outcome == "failed"


async def test_expired_pending_is_not_approvable(test_db, monkeypatch):
    from app.services import proposal as proposal_module

    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    future = now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES + 1)
    monkeypatch.setattr(proposal_module, "now_utc", lambda: future)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="approve", turn_id=turn_id,
        asked_at=future, ledger=ledger,
    )

    assert outcome.outcome == "not_approvable"
    assert outcome.reason == "expired"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_unclear_twice_rejects(test_db):
    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    pending = _pending_view(proposal)
    ledger1, turn1, asked_at1 = await _turn(test_db)

    first = await apply_decision(
        test_db, user, pending=pending, label="unclear", turn_id=turn1,
        asked_at=asked_at1, ledger=ledger1,
    )
    assert first.outcome == "clarify"

    ledger2, turn2, asked_at2 = await _turn(test_db, asked_at=asked_at1 + timedelta(seconds=1))
    second = await apply_decision(
        test_db, user, pending=pending, label="unclear", turn_id=turn2,
        asked_at=asked_at2, ledger=ledger2,
    )
    assert second.outcome == "rejected"
    assert (await test_db["proposals"].find_one({"_id": proposal.id}))["status"] == "rejected"


async def test_none_keeps_pending(test_db):
    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="none", turn_id=turn_id,
        asked_at=asked_at, ledger=ledger,
    )

    assert outcome.outcome == "kept"
    assert (await test_db["proposals"].find_one({"_id": proposal.id}))["status"] == "pending"


async def test_approve_sends_deferred_notices_after_commit(test_db, monkeypatch):
    from app.agents.salon.orchestrator import decide as decide_module

    sent: list = []

    async def fake_send_notices(notices):
        sent.append(list(notices))

    monkeypatch.setattr(decide_module, "send_notices", fake_send_notices)

    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="approve", turn_id=turn_id,
        asked_at=later(asked_at), ledger=ledger,
    )
    await asyncio.sleep(0)  # để task "shield" của _send_notices chạy xong

    assert outcome.outcome == "approved"
    assert len(sent) == 1
    assert len(sent[0]) == 1


async def test_failed_approve_sends_no_notice(test_db, monkeypatch):
    from app.agents.salon.orchestrator import decide as decide_module
    from app.services.appointment import AppointmentService

    sent: list = []

    async def fake_send_notices(notices):
        sent.append(list(notices))

    monkeypatch.setattr(decide_module, "send_notices", fake_send_notices)

    taker = await a_user(test_db, "0938111222", "Cô Hoa")
    await AppointmentService(test_db).create(taker, tomorrow_at(15), None)

    user = await a_user(test_db)
    proposal = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    pending = _pending_view(proposal)
    ledger, turn_id, asked_at = await _turn(test_db)

    outcome = await apply_decision(
        test_db, user, pending=pending, label="approve", turn_id=turn_id,
        asked_at=later(asked_at), ledger=ledger,
    )
    await asyncio.sleep(0)

    assert outcome.outcome == "failed"
    assert sent == []
