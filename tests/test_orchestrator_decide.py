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

from app.agents.booking.context import PendingProposal, TurnContext
from app.agents.booking.ledger import TurnLedger
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

    async def set_pending_outcome(self, turn_id: str, outcome, *, session=None) -> None:
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


# ---------------------------------------------------------------------------
# Task 3.6: ghi lịch + `pending_outcome` là MỘT transaction Mongo thật
# (replica set một node). Dùng `TurnLedger` thật để kiểm cả hai phía cùng
# có hoặc cùng không trên DB.
# ---------------------------------------------------------------------------


class _Hold:
    """Chặn một coroutine tại chỗ cho tới khi task bị huỷ — test chờ bằng
    điều kiện `reached`, không bằng `sleep` cố định."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self._never = asyncio.Event()

    async def here(self) -> None:
        self.reached.set()
        await self._never.wait()


class _SpyChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def appointment_created(self, appointment) -> None:
        self.sent.append(("created", str(appointment.id)))

    async def appointment_cancelled(self, appointment) -> None:
        self.sent.append(("cancelled", str(appointment.id)))

    async def shop_status_changed(self, status) -> None:
        pass


@pytest.fixture
def spy_channel():
    from app.services.notifications import notifications

    saved = list(notifications.channels)
    spy = _SpyChannel()
    notifications.register(spy)
    try:
        yield spy
    finally:
        notifications.channels = saved


async def _real_ledger_deps(db, user, proposal, ledger_cls=TurnLedger):
    ctx = _ctx_with_pending(user, proposal)
    ledger = ledger_cls(db)
    await ledger.start(turn_id="turn-1", user_id=str(user.id), route="orchestrate",
                       rule="", asked_at=ctx.asked_at)
    return _deps(db, user, ctx, ledger), ledger


async def _snapshot(db, user, proposal, ledger) -> dict:
    """Ba phía phải đi cùng nhau: lịch, trạng thái đề xuất, outcome ledger
    (+ dấu hạn mức đặt lịch, cũng ghi trong đường approve)."""
    stored = await db["proposals"].find_one({"_id": proposal.id})
    record = await ledger.record_of("turn-1")
    return {
        "appointments": await db["appointments"].count_documents({"user_id": str(user.id)}),
        "proposal": stored["status"],
        "outcome": (record["pending_outcome"] or {}).get("outcome"),
        "booking_hits": await db["rate_limits"].count_documents(
            {"key": f"booking:user:{user.id}"}),
    }


NOTHING_WRITTEN = {"appointments": 0, "proposal": "pending", "outcome": None, "booking_hits": 0}


async def test_approve_commits_appointment_proposal_and_outcome_together(test_db, spy_channel):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "approved"
    assert await _snapshot(test_db, user, proposal, ledger) == {
        "appointments": 1, "proposal": "approved", "outcome": "approved", "booking_hits": 1}
    assert spy_channel.sent == [("created", outcome.appointment.id)]


class _LedgerDown(TurnLedger):
    """Ghi THẬT (trong session) rồi mới lỗi — để chắc rollback xoá cả lệnh
    ghi ledger lẫn lịch, không chỉ là ledger chưa kịp ghi."""

    async def set_pending_outcome(self, turn_id, outcome, *, session=None) -> None:
        await super().set_pending_outcome(turn_id, outcome, session=session)
        raise RuntimeError("ledger down")


async def test_ledger_failure_rolls_back_the_appointment(test_db, spy_channel):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal, _LedgerDown)

    with pytest.raises(RuntimeError, match="ledger down"):
        await apply_decision(deps, "approve")

    assert await _snapshot(test_db, user, proposal, ledger) == NOTHING_WRITTEN
    assert spy_channel.sent == [], "không báo chủ tiệm một lịch đã rollback"


@pytest.mark.parametrize("cut_at", ["after_claim", "after_ledger_write"])
async def test_cancelling_the_turn_mid_write_leaves_nothing_half_done(
    test_db, spy_channel, monkeypatch, cut_at
):
    """`run_orchestrate` cắt lượt bằng `asyncio.timeout` = huỷ task đang chạy
    `apply_decision`. Huỷ trước commit -> transaction abort -> không còn gì."""
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    hold = _Hold()

    class _LedgerHangs(TurnLedger):
        async def set_pending_outcome(self, turn_id, outcome, *, session=None) -> None:
            await super().set_pending_outcome(turn_id, outcome, session=session)
            await hold.here()

    if cut_at == "after_claim":
        # `_validate_time` đọc giờ mở cửa ngay sau khi claim pending -> approving.
        from app.services.shop import ShopService

        async def hours_hang(self):
            await hold.here()

        monkeypatch.setattr(ShopService, "get_hours", hours_hang)
        deps, ledger = await _real_ledger_deps(test_db, user, proposal)
    else:
        deps, ledger = await _real_ledger_deps(test_db, user, proposal, _LedgerHangs)

    task = asyncio.create_task(apply_decision(deps, "approve"))
    await hold.reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _snapshot(test_db, user, proposal, ledger) == NOTHING_WRITTEN
    assert spy_channel.sent == []


async def test_transient_retry_writes_exactly_once(test_db, spy_channel):
    """`with_transaction` chạy lại callback khi gặp TransientTransactionError —
    lần chạy lại không được tạo lịch thứ hai, ghi thêm dấu hạn mức, hay báo
    chủ tiệm hai lần."""
    from pymongo.errors import OperationFailure

    attempts: list[int] = []

    class _LedgerFlaky(TurnLedger):
        async def set_pending_outcome(self, turn_id, outcome, *, session=None) -> None:
            await super().set_pending_outcome(turn_id, outcome, session=session)
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationFailure("simulated write conflict", code=112,
                                       details={"errorLabels": ["TransientTransactionError"]})

    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal, _LedgerFlaky)

    outcome = await apply_decision(deps, "approve")

    assert len(attempts) == 2
    assert outcome.outcome == "approved"
    assert await _snapshot(test_db, user, proposal, ledger) == {
        "appointments": 1, "proposal": "approved", "outcome": "approved", "booking_hits": 1}
    assert spy_channel.sent == [("created", outcome.appointment.id)]


async def test_taken_slot_records_failed_status_and_outcome(test_db, spy_channel):
    """DuplicateKeyError trong transaction làm Mongo abort cả transaction —
    trạng thái `failed` + outcome `failed` phải vẫn được ghi (transaction
    riêng), đúng như kết quả quan sát được của Task 3.2."""
    user = await a_user(test_db)
    other = await a_user(test_db, name="Co Hoa")
    start = _tomorrow_at(15)
    await AppointmentService(test_db).create(other, start, None)
    spy_channel.sent.clear()
    proposal = await _make_pending_proposal(test_db, user, start_at=start)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "failed"
    assert outcome.error
    assert 1 <= len(outcome.nearest) <= 3
    assert all(m.iso != start.isoformat() for m in outcome.nearest)
    stored = await test_db["proposals"].find_one({"_id": proposal.id})
    assert stored["error"] == outcome.error
    snap = await _snapshot(test_db, user, proposal, ledger)
    assert (snap["appointments"], snap["proposal"], snap["outcome"]) == (0, "failed", "failed")
    assert spy_channel.sent == []


async def test_failed_status_rolls_back_with_its_ledger_write(test_db, spy_channel):
    user = await a_user(test_db)
    other = await a_user(test_db, name="Co Hoa")
    start = _tomorrow_at(15)
    await AppointmentService(test_db).create(other, start, None)
    proposal = await _make_pending_proposal(test_db, user, start_at=start)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal, _LedgerDown)

    with pytest.raises(RuntimeError, match="ledger down"):
        await apply_decision(deps, "approve")

    snap = await _snapshot(test_db, user, proposal, ledger)
    assert (snap["appointments"], snap["proposal"], snap["outcome"]) == (0, "pending", None)


async def test_expired_records_status_and_not_approvable_together(test_db):
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    await test_db["proposals"].update_one(
        {"_id": proposal.id}, {"$set": {"expires_at": now_utc() - timedelta(minutes=1)}})
    deps, ledger = await _real_ledger_deps(test_db, user, proposal)

    outcome = await apply_decision(deps, "approve")

    assert (outcome.outcome, outcome.reason) == ("not_approvable", "expired")
    snap = await _snapshot(test_db, user, proposal, ledger)
    assert (snap["appointments"], snap["proposal"], snap["outcome"]) == (0, "expired", "not_approvable")


@pytest.mark.parametrize("label, status, recorded", [
    ("reject", "rejected", "rejected"),
    ("change", "superseded", "superseded"),
    ("unclear", "pending", "clarify"),
])
async def test_other_labels_roll_back_with_a_failed_ledger_write(test_db, label, status, recorded):
    """Đóng đề xuất (reject/change/unclear) cũng đi chung transaction với
    ledger: ledger lỗi -> đề xuất giữ nguyên `pending`, `unclear_count` không tăng."""
    user = await a_user(test_db)
    proposal = await _make_pending_proposal(test_db, user)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal, _LedgerDown)

    with pytest.raises(RuntimeError, match="ledger down"):
        await apply_decision(deps, label)

    stored = await test_db["proposals"].find_one({"_id": proposal.id})
    assert (stored["status"], stored["unclear_count"]) == ("pending", 0)

    deps_ok, ledger_ok = await _real_ledger_deps(test_db, user, proposal)
    await apply_decision(deps_ok, label)
    stored = await test_db["proposals"].find_one({"_id": proposal.id})
    record = await ledger_ok.record_of("turn-1")
    assert (stored["status"], record["pending_outcome"]["outcome"]) == (status, recorded)


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
        async def set_pending_outcome(self, turn_id, outcome, *, session=None):
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


@pytest.mark.parametrize("new_minute", [15, 0])
async def test_move_in_one_transaction_cancels_old_and_books_new(test_db, spy_channel, new_minute):
    """Dời lịch trong transaction: huỷ cũ + đặt mới cùng commit. `15` = giờ mới
    chồng lên chính lịch cũ (nhả cũ trước — index partial phải thấy lệnh huỷ
    chưa commit của CHÍNH transaction); `0` (17:00) = không chồng."""
    user = await a_user(test_db)
    old = await AppointmentService(test_db).create(user, _tomorrow_at(15), "sơn gel")
    spy_channel.sent.clear()
    new_start = _tomorrow_at(15, new_minute) if new_minute else _tomorrow_at(17)
    proposal = await ProposalService(test_db).create(user, "move", new_start, None, target=old)
    deps, ledger = await _real_ledger_deps(test_db, user, proposal)

    outcome = await apply_decision(deps, "approve")

    assert outcome.outcome == "approved"
    booked = await test_db["appointments"].find(
        {"user_id": str(user.id), "status": "booked"}).to_list(None)
    assert [b["start_at"] for b in booked] == [new_start]
    assert sorted(spy_channel.sent) == sorted(
        [("created", outcome.appointment.id), ("cancelled", str(old.id))])
    record = await ledger.record_of("turn-1")
    assert record["pending_outcome"]["outcome"] == "approved"
