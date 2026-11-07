"""Task 4.5: `button_path.py` — đường nút Đồng ý/Không, không qua Orchestrator.

Chỉ kiểm phần agent THÊM vào trên `apply_decision` (đã kiểm kỹ ở
`tests/test_orchestrator_decide.py`): ánh xạ outcome -> `ButtonOutcome.kind`,
và nhánh `stale` không chạm DB/ledger khi id không khớp.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from bson import ObjectId

from app.agents.booking.button_path import run_button, stale_reply
from app.agents.booking.context import PendingProposal
from app.agents.booking.orchestrator.tools import OrchestratorDeps
from app.agents.booking.reply_brief import ButtonOutcome
from app.agents.booking.button import ButtonReply
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import ProposalService
from tests.factories import make_ctx


def _tomorrow_at(hour: int, minute: int = 0):
    """Mốc giờ Việt Nam CỐ ĐỊNH (mai) — không lệ thuộc giờ chạy test thật, cùng
    mẫu `tests/test_orchestrator_decide.py`."""
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


class FakeLedger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set_pending_outcome(self, turn_id, outcome, *, session=None) -> None:
        self.calls.append((turn_id, outcome))


async def a_user(db, name="Cô Lan"):
    phone = f"09{uuid.uuid4().int % 100000000:08d}"
    return await AuthService(db).create_user(phone, "matkhau123", name)


def _deps(db, user, ctx, ledger=None) -> OrchestratorDeps:
    return OrchestratorDeps(
        db=db,
        user=user,
        ctx=ctx,
        ledger=ledger or FakeLedger(),
        turn_id="turn-1",
        question="",
        max_rounds=8,
        run_appointment=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unused")),
        run_shop=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unused")),
    )


async def _make_pending_proposal(db, user, *, start_at=None):
    start = start_at or _tomorrow_at(15)
    return await ProposalService(db).create(user, "book", start, None)


def _pending_from(proposal) -> PendingProposal:
    return PendingProposal(
        id=str(proposal.id),
        kind=proposal.kind,
        start_at=proposal.start_at,
        spoken=format_vi_datetime(proposal.start_at),
        note=proposal.note,
        expires_at=proposal.expires_at,
        unclear_count=proposal.unclear_count,
        target=None,
    )


def _ctx_with_pending(pending: PendingProposal, *, asked_at=None):
    return make_ctx(pending=pending, asked_at=asked_at or now_utc())


async def test_yes_on_the_pending_proposal_approves_and_writes(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    pending = _pending_from(proposal)
    # Lượt SAU (`ProposalService.approve` chỉ duyệt đề xuất tạo TRƯỚC `asked_at`).
    ctx = _ctx_with_pending(pending, asked_at=proposal.created_at + timedelta(seconds=1))
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)
    button = ButtonReply(decision="yes", proposal_id=pending.id, label="Đồng ý")

    result = await run_button(deps, button)

    assert result.kind == "approved"
    assert result.outcome is not None and result.outcome.appointment is not None
    appts = await AppointmentService(test_db).upcoming_for(user)
    assert any(str(a.id) == result.outcome.appointment.id for a in appts)
    assert ledger.calls and ledger.calls[0][1] is result.outcome


async def test_no_rejects_without_writing(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    pending = _pending_from(proposal)
    ctx = _ctx_with_pending(pending, asked_at=proposal.created_at + timedelta(seconds=1))
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)
    button = ButtonReply(decision="no", proposal_id=pending.id, label="Không")

    result = await run_button(deps, button)

    assert result.kind == "rejected"
    appts = await AppointmentService(test_db).upcoming_for(user)
    assert appts == []
    assert ledger.calls and ledger.calls[0][1] is result.outcome


async def test_mismatched_id_is_stale_and_touches_nothing(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    pending = _pending_from(proposal)
    ctx = _ctx_with_pending(pending, asked_at=proposal.created_at + timedelta(seconds=1))
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)
    button = ButtonReply(decision="yes", proposal_id="not-the-pending-id", label="Đồng ý")

    result = await run_button(deps, button)

    assert result == ButtonOutcome(kind="stale", outcome=None)
    assert ledger.calls == []
    # Đề xuất gốc vẫn còn `pending`, không bị đụng tới.
    reloaded = await ProposalService(test_db).get(user, str(proposal.id))
    assert reloaded is not None and reloaded.status == "pending"


async def test_no_pending_is_stale_and_touches_nothing(test_db, clean_db):
    user = await a_user(test_db)
    ctx = make_ctx(pending=None)
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)
    button = ButtonReply(decision="yes", proposal_id="whatever", label="Đồng ý")

    result = await run_button(deps, button)

    assert result == ButtonOutcome(kind="stale", outcome=None)
    assert ledger.calls == []


async def test_yes_on_expired_is_not_approvable(test_db, clean_db):
    """Dù id khớp, đề xuất ĐÃ HẾT HẠN vẫn phải đi qua `apply_decision` (không
    được short-circuit thành stale) để ledger ghi lại `not_approvable`
    (reason="expired")."""
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    expired_at = now_utc() - timedelta(minutes=1)
    # Đề xuất THẬT trong DB cũng phải hết hạn — `apply_decision` đọc hạn từ
    # document, không phải từ `PendingProposal` của `ctx`.
    await test_db.proposals.update_one(
        {"_id": ObjectId(proposal.id)}, {"$set": {"expires_at": expired_at}}
    )
    pending = _pending_from(proposal).model_copy(update={"expires_at": expired_at})
    ctx = _ctx_with_pending(pending, asked_at=proposal.created_at + timedelta(seconds=1))
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)
    button = ButtonReply(decision="yes", proposal_id=pending.id, label="Đồng ý")

    result = await run_button(deps, button)

    assert result.kind == "not_approvable"
    assert result.outcome is not None and result.outcome.reason == "expired"
    assert ledger.calls and ledger.calls[0][1] is result.outcome


def test_stale_reply_uses_the_address():
    text = stale_reply("chị Lan")
    assert "chị Lan" in text
    assert "anh chị" not in text
