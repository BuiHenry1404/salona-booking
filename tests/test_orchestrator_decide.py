"""Task 3.2: `orchestrator/decide.py` — veto có cấu trúc (§9.3 (i)) và cổng
ghi duy nhất (§5.5) của Orchestrator.

`veto()` là hàm thuần, kiểm bằng `PendingProposal` giả, không chạm Mongo.
`apply_decision()` là nơi duy nhất trong `app/agents/` (gói mới) gọi
`ProposalService.approve` — kiểm bằng Mongo thật (DB `chatbot_test_db`,
`AuthService.create_user` như `tests/test_specialist_appointment.py`).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

import app.agents.booking.orchestrator.decide as decide_module
from app.agents.booking.context import PendingProposal, TurnContext
from app.agents.booking.orchestrator.decide import DecideRejected, apply_decision, veto
from app.agents.booking.orchestrator.tools import OrchestratorDeps, make_orchestrator_tools
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import PROPOSAL_MAX_AGE_MINUTES, UNCLEAR_LIMIT, ProposalService
from tests.factories import make_ctx


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    """Mốc giờ Việt Nam CỐ ĐỊNH (mai, `hour`:`minute`) — không lệ thuộc giờ
    chạy test thật: review M3 chỉ ra `now_utc() + timedelta(hours=5)` rơi
    ngoài giờ mở cửa (08:00-19:00) tuỳ lúc chạy CI, làm các test approve/veto
    flaky theo đồng hồ."""
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


def _pending(**overrides) -> PendingProposal:
    start = _tomorrow_at(15)
    defaults = dict(
        id="p1",
        kind="book",
        start_at=start,
        spoken=format_vi_datetime(start),
        note=None,
        expires_at=now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES),
        unclear_count=0,
        target=None,
    )
    defaults.update(overrides)
    return PendingProposal(**defaults)


# ---------------------------------------------------------------------------
# veto() — hàm thuần, không chạm Mongo
# ---------------------------------------------------------------------------


def test_veto_changes_approve_when_another_time_is_named():
    pending = _pending()
    v = veto("approve", None, "ừ mà 4 giờ nha", pending, now_utc())
    assert v.label == "change"
    assert v.reject_code is None


def test_veto_rejects_change_that_restates_the_same_time():
    pending = _pending()
    # Dùng chính `spoken` của pending để chắc chắn cùng giờ, không lệ thuộc múi giờ.
    v = veto("change", None, f"đổi qua {pending.spoken} nha", pending, now_utc())
    assert v.reject_code == "same_time_as_pending"


@pytest.mark.parametrize(
    "text",
    [
        "thôi đổi lịch khác đi",  # muốn đổi nhưng không nêu giờ nào
        "đổi giờ khác nha",
        "lùi lại 30 phút",  # tương đối, không phải một mốc giờ cụ thể
    ],
)
def test_veto_keeps_change_that_names_no_concrete_time(text):
    """Review M3 finding 1: §9.3 (i) chỉ từ chối khi câu khách NHẮC LẠI đúng
    giờ đề xuất (`restates_proposal_time`), không phải mọi câu "change" không
    nêu giờ khác (`not other_time`) — nếu không, "đổi giờ khác đi" (không nêu
    giờ nào) cũng bị từ chối oan, dù khách rõ ràng muốn đổi."""
    pending = _pending()
    v = veto("change", None, text, pending, now_utc())
    assert v.label == "change"
    assert v.reject_code is None


def test_veto_ignores_a_time_that_belongs_to_a_queued_request():
    """Review M3 finding 4: một giờ chỉ thuộc về lịch ĐANG CHỜ (hàng chờ,
    `ctx.queued_requests`) không được hiểu nhầm là khách đang đổi giờ của
    chính đề xuất đang chờ duyệt."""
    pending = _pending()
    queued = [_tomorrow_at(14)]
    v = veto("approve", None, "ừ, còn lịch 2 giờ thì để sau nha", pending, now_utc(), queued=queued)
    assert v.label == "approve"
    assert v.reject_code is None


def test_veto_downgrades_approve_with_relative_free_to_unclear():
    pending = _pending()
    v = veto("approve", "relative_free", "để em hỏi người nhà coi rảnh không đã", pending, now_utc())
    assert v.label == "unclear"
    assert v.reject_code is None


@pytest.mark.parametrize("aside", ["defer_only", "defer_with_request"])
def test_veto_downgrades_approve_with_defer_to_none(aside):
    pending = _pending()
    v = veto("approve", aside, "để lát em nhắn lại", pending, now_utc())
    assert v.label == "none"
    assert v.reject_code is None


@pytest.mark.parametrize(
    "label,aside,text,expected_label",
    [
        ("reject", None, "thôi khỏi", "reject"),
        ("none", None, "4 giờ chiều mai đi", "none"),
    ],
)
def test_veto_never_upgrades(label, aside, text, expected_label):
    pending = _pending()
    v = veto(label, aside, text, pending, now_utc())
    assert v.label == expected_label
    assert v.reject_code is None


# ---------------------------------------------------------------------------
# apply_decision() — chạm Mongo, cổng ghi duy nhất
# ---------------------------------------------------------------------------


class FakeLedger:
    """Ghi lại `set_pending_outcome`, kiểm thứ tự ghi trước khi `apply_decision` trả về."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set_pending_outcome(self, turn_id: str, outcome) -> None:
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
    proposal = await ProposalService(db).create(user, "book", start, None)
    return proposal


def _ctx_with_pending(user, proposal) -> TurnContext:
    pending = PendingProposal(
        id=str(proposal.id),
        kind=proposal.kind,
        start_at=proposal.start_at,
        spoken=format_vi_datetime(proposal.start_at),
        note=proposal.note,
        expires_at=proposal.expires_at,
        unclear_count=proposal.unclear_count,
        target=None,
    )
    # `ProposalService.approve` chỉ duyệt đề xuất tạo TRƯỚC `turn_at` (lượt
    # SAU mới được chốt) — cộng thêm biên an toàn để không rơi vào "same_turn"
    # do trùng mili-giây giữa lúc tạo đề xuất và lúc dựng `ctx` trong test.
    asked_at = proposal.created_at + timedelta(seconds=1)
    return make_ctx(pending=pending, asked_at=asked_at)


async def test_approve_writes_the_appointment_and_the_ledger(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "approved"
    assert outcome.appointment is not None
    appts = await AppointmentService(test_db).upcoming_for(user)
    assert any(str(a.id) == outcome.appointment.id for a in appts)
    assert ledger.calls and ledger.calls[0][1] is outcome


async def test_approve_survives_cancellation_right_after_it_starts(test_db, clean_db, monkeypatch):
    """Fix round 1, Minor: huỷ TASK NGOÀI (như `asyncio.timeout` cắt lượt ở
    `run_orchestrate`) ngay SAU khi ghi lịch bắt đầu không được làm mất đề
    xuất đã ghi mà `turn_ledger` không bao giờ nghe — anti-goal "lịch đã ghi
    mà khách không được báo". `apply_decision` bọc đoạn ghi lịch + ghi ledger
    bằng `asyncio.shield` nên phần đó chạy tới nơi tới chốn dù awaiter của nó
    (ở đây là `task`) bị huỷ giữa chừng."""
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    ledger = FakeLedger()
    deps = _deps(test_db, user, ctx, ledger)

    started = asyncio.Event()
    original_apply_approve = decide_module._apply_approve

    async def slow_apply_approve(db, user, ctx, pending):
        started.set()
        await asyncio.sleep(0.05)
        return await original_apply_approve(db, user, ctx, pending)

    monkeypatch.setattr(decide_module, "_apply_approve", slow_apply_approve)

    task = asyncio.create_task(apply_decision(deps, "approve"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Nhường event loop để coroutine được `shield` (chạy nền, không bị huỷ
    # theo `task`) kịp ghi xong lịch + ledger.
    await asyncio.sleep(0.1)

    assert ledger.calls and ledger.calls[0][1].outcome == "approved"
    appts = await AppointmentService(test_db).upcoming_for(user)
    assert appts, "lich van phai duoc ghi du luot goi no bi huy"


async def test_approve_on_a_taken_slot_is_failed_with_nearest(test_db, clean_db):
    user = await a_user(test_db)
    other = await a_user(test_db, name="Co Hoa")
    start = _tomorrow_at(15)
    # Lịch của NGƯỜI KHÁC chiếm đúng giờ pending sẽ xin duyệt.
    await AppointmentService(test_db).create(other, start, None)

    proposal = await _make_pending_proposal(test_db, user, start_at=start)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "failed"
    assert len(outcome.nearest) <= 3


async def test_approve_after_expiry_is_not_approvable(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    # `asked_at` TRƯỚC khi đề xuất hết hạn nhưng ta giả lập hạn đã qua bằng
    # cách đặt `expires_at` của PendingProposal vào quá khứ: `apply_decision`
    # gọi thẳng `ProposalService.approve`, hạn thật đọc từ DB (`proposals`),
    # nên phải cập nhật DB để mô phỏng đề xuất đã hết hạn.
    await test_db["proposals"].update_one(
        {"_id": proposal.id},
        {"$set": {"expires_at": now_utc() - timedelta(minutes=1)}},
    )
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "not_approvable"
    assert outcome.reason == "expired"
    appts = await AppointmentService(test_db).upcoming_for(user)
    assert appts == []


async def test_unclear_first_time_is_clarify_second_time_rejects(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    first = await apply_decision(deps, "unclear")
    assert first.outcome == "clarify"

    second = await apply_decision(deps, "unclear")
    assert second.outcome == "rejected"


async def test_unclear_on_an_already_closed_proposal_is_not_approvable(test_db, clean_db):
    """Review M3 finding 3: `mark_unclear` không tìm thấy đề xuất PENDING (đã
    bị đóng bởi nút bấm, lượt khác, hoặc hết hạn) phải báo `not_approvable`,
    không phải `clarify` cho một đề xuất không còn tồn tại để hỏi lại."""
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)
    assert await ProposalService(test_db).reject(user, str(proposal.id))

    outcome = await apply_decision(deps, "unclear")

    assert outcome.outcome == "not_approvable"
    assert outcome.reason == "rejected"


async def test_none_keeps_the_proposal_untouched(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    outcome = await apply_decision(deps, "none")

    assert outcome.outcome == "kept"
    stored = await ProposalService(test_db).get(user, str(proposal.id))
    assert stored.status == "pending"


async def test_ledger_has_the_outcome_before_apply_returns(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)

    order: list[str] = []

    class OrderingLedger:
        async def set_pending_outcome(self, turn_id, outcome):
            order.append("ledger")

    deps = _deps(test_db, user, ctx, OrderingLedger())

    outcome = await apply_decision(deps, "reject")
    order.append("returned")

    assert order == ["ledger", "returned"]
    assert outcome.outcome == "rejected"


async def test_reject_and_change_close_the_proposal(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    outcome = await apply_decision(deps, "reject")
    assert outcome.outcome == "rejected"
    stored = await ProposalService(test_db).repo.get_owned(str(user.id), str(proposal.id))
    assert stored.status == "rejected"


async def test_change_supersedes_the_proposal(test_db, clean_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    ctx = _ctx_with_pending(user, proposal)
    deps = _deps(test_db, user, ctx)

    outcome = await apply_decision(deps, "change")
    assert outcome.outcome == "superseded"
    stored = await ProposalService(test_db).repo.get_owned(str(user.id), str(proposal.id))
    assert stored.status == "superseded"


async def test_decide_pending_rejects_before_veto_when_nothing_is_pending():
    """Review M3 finding 5: `veto()` đọc thẳng `pending.start_at` — gọi nó
    khi `ctx.pending is None` (lẽ ra guard (h) của Task 3.3 đã chặn từ trước)
    phải dừng ở `decide_pending`, KHÔNG để lọt xuống `veto()`/`apply_decision`
    rồi văng `AttributeError`."""
    ctx = make_ctx(pending=None)
    deps = _deps(db=None, user=None, ctx=ctx)
    tools = {t.name: t for t in make_orchestrator_tools(deps)}

    with pytest.raises(DecideRejected) as exc_info:
        await tools["decide_pending"].ainvoke(
            {"label": "approve", "aside": None, "reason": "khách nói ừ"}
        )
    assert exc_info.value.reject_code == "no_pending"
