"""Task 1.2: `TurnContext` và `render_context` hai mức (lite/full)."""
from __future__ import annotations

import inspect
import re
from datetime import timedelta

from datetime import timedelta as _timedelta

import pytest

from app.agents.booking.context import (
    NOT_RENDERED,
    AppointmentRef,
    CustomerRef,
    OpenQuestion,
    PendingProposal,
    PreviousCall,
    PreviousTurn,
    TurnContext,
    UnannouncedOutcome,
    load_turn_context,
    render_context,
)
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.schemas import Moment, PendingOutcome
from app.core.clock import now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.models.conversation import ConversationSlots, GoalProgress, QueuedRequest
from app.models.shop import ShopHours, ShopStatusView
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation_state import ConversationStateService
from app.services.proposal import ProposalService

from tests.factories import make_ctx

HEX24 = re.compile(r"\b[0-9a-f]{24}\b")


def _future(hours: int):
    return to_local(now_utc()) + timedelta(hours=hours)


def test_render_context_has_no_customer_text_param():
    """`render_context` không nhận chữ khách — chỉ (ctx, level)."""
    sig = inspect.signature(render_context)
    assert list(sig.parameters) == ["ctx", "level"]


def test_customer_text_is_not_part_of_render():
    """`TurnContext` không có trường chữ khách thô nào để lọt vào render."""
    ctx = make_ctx()
    text = render_context(ctx, "full")
    assert "customer_text" not in text.lower()


def test_lite_render_has_pending_and_goal_but_no_upcoming_list():
    pending = PendingProposal(
        id="aaaaaaaaaaaaaaaaaaaaaaaa",
        kind="book",
        start_at=_future(3),
        spoken="3 giờ chiều thứ Sáu",
        expires_at=_future(0),
        unclear_count=0,
    )
    upcoming = [
        AppointmentRef(id="bbbbbbbbbbbbbbbbbbbbbbbb", start_at=_future(48), spoken="Thứ Bảy 10 giờ sáng")
    ]
    ctx = make_ctx(pending=pending, upcoming=upcoming)

    lite = render_context(ctx, "lite")

    assert "Pending proposal" in lite
    assert pending.id in lite
    assert "Goal" in lite
    assert upcoming[0].id not in lite


def test_full_render_lists_every_upcoming_id_and_iso():
    upcoming = [
        AppointmentRef(id="bbbbbbbbbbbbbbbbbbbbbbbb", start_at=_future(24), spoken="Thứ Sáu 9 giờ sáng"),
        AppointmentRef(id="cccccccccccccccccccccccc", start_at=_future(72), spoken="Chủ Nhật 2 giờ chiều"),
    ]
    ctx = make_ctx(upcoming=upcoming)

    full = render_context(ctx, "full")

    for a in upcoming:
        assert a.id in full
        assert a.start_at.isoformat() in full


def test_unannounced_comes_first_in_full():
    unannounced = UnannouncedOutcome(
        proposal_id="dddddddddddddddddddddddd",
        outcome="approved",
        kind="book",
        spoken="3 giờ chiều thứ Sáu",
    )
    ctx = make_ctx(unannounced=unannounced)

    full = render_context(ctx, "full")
    first_line = full.splitlines()[0]

    assert "Unannounced outcome" in first_line
    assert unannounced.proposal_id in first_line


def test_render_never_invents_ids():
    """Mọi chuỗi 24-hex trong output phải thuộc tập id có trong ctx."""
    pending = PendingProposal(
        id="aaaaaaaaaaaaaaaaaaaaaaaa",
        kind="move",
        start_at=_future(5),
        spoken="5 giờ chiều mai",
        expires_at=_future(0),
        unclear_count=1,
        target=AppointmentRef(id="eeeeeeeeeeeeeeeeeeeeeeee", start_at=_future(1), spoken="hôm nay 1 giờ"),
    )
    declined = PendingProposal(
        id="ffffffffffffffffffffffff",
        kind="book",
        start_at=_future(6),
        spoken="6 giờ chiều mai",
        expires_at=_future(0),
        unclear_count=0,
    )
    upcoming = [AppointmentRef(id="bbbbbbbbbbbbbbbbbbbbbbbb", start_at=_future(24), spoken="mai 9 giờ")]
    unannounced = UnannouncedOutcome(
        proposal_id="111111111111111111111111", outcome="failed", kind="cancel", spoken="hôm nay", error="x"
    )
    previous_turns = [
        PreviousTurn(
            turn_id="t0",
            route="orchestrate",
            reply_kind="proposal_made",
            calls=[
                PreviousCall(
                    action="find_slots",
                    status="ok",
                    offered=[Moment(iso="2026-01-01T09:00:00", spoken="9 giờ sáng")],
                    resolved=Moment(iso="2026-01-01T09:00:00", spoken="9 giờ sáng"),
                    proposal=PendingProposal(
                        id="222222222222222222222222",
                        kind="book",
                        start_at=_future(2),
                        spoken="2 giờ chiều",
                        expires_at=_future(0),
                        unclear_count=0,
                    ),
                )
            ],
        )
    ]
    ctx = make_ctx(
        pending=pending,
        recently_declined=declined,
        upcoming=upcoming,
        unannounced=unannounced,
        previous_turns=previous_turns,
    )

    known_ids = {
        ctx.customer.id,
        pending.id,
        pending.target.id,
        declined.id,
        upcoming[0].id,
        unannounced.proposal_id,
        previous_turns[0].calls[0].proposal.id,
    }

    full = render_context(ctx, "full")
    found = set(HEX24.findall(full))
    assert found <= known_ids
    assert found  # ngữ cảnh này phải có ít nhất một id xuất hiện


def test_previous_turn_calls_show_offered_and_resolved():
    call = PreviousCall(
        action="find_slots",
        status="ok",
        offered=[Moment(iso="2026-01-01T09:00:00", spoken="9 giờ sáng"), Moment(iso="2026-01-01T10:00:00", spoken="10 giờ sáng")],
        resolved=Moment(iso="2026-01-01T09:00:00", spoken="9 giờ sáng"),
    )
    turn = PreviousTurn(turn_id="t0", route="orchestrate", reply_kind="answer", calls=[call])
    ctx = make_ctx(previous_turns=[turn])

    full = render_context(ctx, "full")

    assert "find_slots" in full
    assert "ok" in full
    assert "9 giờ sáng" in full
    assert "10 giờ sáng" in full


def test_previous_turn_call_renders_partial_date():
    """Task 5.5 fix round 1 (I-3): một lượt trước chỉ chốt được ngày (chưa đủ
    giờ) phải để lại dấu vết trong khối bối cảnh, để Orchestrator đọc được
    ngày đó ở lượt sau."""
    from datetime import date

    call = PreviousCall(action="resolve_time", status="needs_info", partial_date=date(2026, 1, 2))
    turn = PreviousTurn(turn_id="t0", route="orchestrate", reply_kind="clarify", calls=[call])
    ctx = make_ctx(previous_turns=[turn])

    full = render_context(ctx, "full")

    assert "2026-01-02" in full


def test_previous_turn_call_renders_dash_when_no_partial_date():
    call = PreviousCall(action="find_slots", status="ok")
    turn = PreviousTurn(turn_id="t0", route="orchestrate", reply_kind="answer", calls=[call])
    ctx = make_ctx(previous_turns=[turn])

    full = render_context(ctx, "full")

    assert "partial_date=-" in full


def test_full_render_covers_every_turn_context_field():
    """Mọi trường của `TurnContext` ngoài `NOT_RENDERED` phải để lại dấu vết
    trong `full`. Thêm trường mới vào `TurnContext` mà quên render là test
    này phải đỏ."""
    customer = CustomerRef(
        id="c1c1c1c1c1c1c1c1c1c1c1c1",
        display_name="Trần Thị Marker",
        address_phrase="chị Marker",
        staff_note="ghi chú nhân viên marker",
    )
    shop_status = ShopStatusView(is_busy=True)
    shop_hours = ShopHours(open_time="08:00", close_time="19:00")
    upcoming = [
        AppointmentRef(id="u1u1u1u1u1u1u1u1u1u1u1u1", start_at=_future(10), spoken="marker-upcoming-spoken")
    ]
    recent_visit = _future(-240).date()
    pending = PendingProposal(
        id="p1p1p1p1p1p1p1p1p1p1p1p1",
        kind="book",
        start_at=_future(2),
        spoken="marker-pending-spoken",
        expires_at=_future(0),
        unclear_count=2,
    )
    recently_declined = PendingProposal(
        id="d1d1d1d1d1d1d1d1d1d1d1d1",
        kind="cancel",
        start_at=_future(1),
        spoken="marker-declined-spoken",
        expires_at=_future(0),
        unclear_count=0,
    )
    unannounced = UnannouncedOutcome(
        proposal_id="a1a1a1a1a1a1a1a1a1a1a1a1", outcome="approved", kind="book", spoken="marker-unannounced-spoken"
    )
    queued = [
        QueuedRequest(
            queue_id="q1q1q1q1q1q1q1q1q1q1q1q1",
            start_at=_future(5),
            spoken="marker-queued-spoken",
            queued_at=to_local(now_utc()),
        )
    ]
    open_question = OpenQuestion(field="time", asked_at=to_local(now_utc()), times=3)
    previous_turns = [
        PreviousTurn(
            turn_id="t9",
            route="marker-route",
            reply_kind="marker-reply-kind",
            calls=[PreviousCall(action="marker-action", status="ok")],
        )
    ]
    goal = GoalProgress(
        goal_id="marker-goal-id",
        turns_without_progress=7,
        last_marker="marker-last-marker",
        service="marker-goal-service",
        appointment_id="marker-goal-appointment-id",
    )
    slots = ConversationSlots(service="marker-slots-service")

    ctx = make_ctx(
        customer=customer,
        shop_status=shop_status,
        shop_hours=shop_hours,
        upcoming=upcoming,
        recent_visits=[recent_visit],
        pending=pending,
        recently_declined=recently_declined,
        unannounced=unannounced,
        queued_requests=queued,
        open_question=open_question,
        answers_open_question_hint=True,
        last_bot_text="marker-last-bot-text",
        previous_turns=previous_turns,
        goal=goal,
        slots=slots,
        digest_text="marker-digest-text",
    )

    full = render_context(ctx, "full")

    marker_of_field = {
        "now_local": format_vi_datetime(ctx.now_local),
        "today": ctx.today.isoformat(),
        "calendar_14d": ctx.calendar_14d[0][1],
        "customer": customer.display_name,
        "shop_status": str(shop_status.is_busy),
        "shop_hours": shop_hours.open_time,
        "upcoming": upcoming[0].id,
        "recent_visits": recent_visit.isoformat(),
        "pending": pending.spoken,
        "recently_declined": recently_declined.spoken,
        "unannounced": unannounced.spoken,
        "queued_requests": queued[0].spoken,
        "open_question": "time",
        "answers_open_question_hint": "True",
        "last_bot_text": "marker-last-bot-text",
        "previous_turns": "marker-action",
        "goal": "marker-goal-id",
        "slots": "marker-slots-service",
        "digest_text": "marker-digest-text",
    }

    rendered_fields = set(TurnContext.model_fields) - NOT_RENDERED
    assert rendered_fields == set(marker_of_field), (
        "cập nhật test: TurnContext có trường mới chưa có marker để kiểm render"
    )
    for field_name, marker in marker_of_field.items():
        assert marker in full, f"trường '{field_name}' không thấy dấu vết trong full render"


def test_open_question_and_queued_counted_in_lite():
    ctx = make_ctx(
        open_question=OpenQuestion(field="time", asked_at=to_local(now_utc()), times=1),
        queued_requests=[],
    )
    lite = render_context(ctx, "lite")
    assert "Open question" in lite
    assert "time" in lite


# ---------------------------------------------------------------------------
# Task 1.4: `load_turn_context` — nạp từ DB
# ---------------------------------------------------------------------------


async def _a_user(db, phone="0912345678", name="Nguyễn Thị Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


def _tomorrow_at(hour: int):
    """Mốc trong giờ mở cửa mặc định (08:00–19:00) của ngày mai, giờ VN."""
    local = to_local(now_utc()) + _timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def test_calendar_has_fourteen_days_starting_today(test_db):
    user = await _a_user(test_db)
    asked_at = now_utc()

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=asked_at)

    assert len(ctx.calendar_14d) == 14
    assert ctx.calendar_14d[0][0] == to_local(asked_at).date()
    assert ctx.calendar_14d[-1][0] == to_local(asked_at).date() + _timedelta(days=13)
    assert ctx.today == to_local(asked_at).date()


async def test_pending_is_set_only_when_latest_is_pending_and_unexpired(test_db):
    user = await _a_user(test_db)
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(15), "làm tóc")

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert ctx.pending is not None
    assert ctx.pending.id == str(proposal.id)
    assert ctx.pending.kind == "book"
    assert ctx.recently_declined is None


async def test_pending_is_none_once_the_proposal_has_expired(test_db):
    user = await _a_user(test_db)
    proposals = ProposalService(test_db)
    await proposals.create(user, "book", _tomorrow_at(15), None)

    # Lượt sau, quá 10 phút kể từ lúc tạo đề xuất — "pending" trong DB nhưng
    # đã hết hạn, nên KHÔNG được coi là pending ở khối bối cảnh.
    asked_at = now_utc() + _timedelta(minutes=11)
    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=asked_at)

    assert ctx.pending is None


async def test_recently_declined_within_ten_minutes(test_db):
    user = await _a_user(test_db)
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(16), None)
    await proposals.reject(user, str(proposal.id))

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert ctx.recently_declined is not None
    assert ctx.recently_declined.id == str(proposal.id)
    assert ctx.pending is None


async def test_recently_declined_is_none_after_ten_minutes(test_db):
    user = await _a_user(test_db)
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(16), None)
    await proposals.reject(user, str(proposal.id))

    asked_at = now_utc() + _timedelta(minutes=11)
    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=asked_at)

    assert ctx.recently_declined is None


async def test_upcoming_capped_at_five(test_db):
    user = await _a_user(test_db)
    appointments = AppointmentService(test_db)
    for hour in (8, 9, 10, 11, 12, 13, 14):
        await appointments.create(user, _tomorrow_at(hour), None)

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert len(ctx.upcoming) == 5


async def test_recent_visits_capped_at_three(test_db):
    user = await _a_user(test_db)
    appointments = AppointmentService(test_db)
    for days_ago in (1, 2, 3, 4):
        past = to_local(now_utc()) - _timedelta(days=days_ago)
        await appointments.repo.collection.insert_one({
            "user_id": str(user.id),
            "start_at": past.astimezone(),
            "duration_minutes": 60,
            "slot_keys": [f"test-recent-visit-{days_ago}"],
            "note": None,
            "status": "booked",
            "created_via": "chat",
            "created_at": past,
            "updated_at": past,
        })

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert len(ctx.recent_visits) <= 3


async def test_open_question_comes_from_previous_clarify_turn(test_db):
    user = await _a_user(test_db)
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    first_asked = asked_at - _timedelta(minutes=2)
    await ledger.start(turn_id="prev", user_id=str(user.id), route="orchestrate",
                       rule="R5", asked_at=first_asked)
    await ledger.set_ask("prev", "time")
    await ledger.finish("prev", ended_by="ask_customer", reply_kind="clarify")

    ctx = await load_turn_context(test_db, user, turn_id="t2", asked_at=asked_at)

    assert ctx.open_question is not None
    assert ctx.open_question.field == "time"
    assert ctx.open_question.times == 1


async def test_open_question_times_two_after_two_consecutive_same_field(test_db):
    user = await _a_user(test_db)
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    t_minus_2 = asked_at - _timedelta(minutes=4)
    t_minus_1 = asked_at - _timedelta(minutes=2)

    await ledger.start(turn_id="turn-a", user_id=str(user.id), route="orchestrate",
                       rule="R5", asked_at=t_minus_2)
    await ledger.set_ask("turn-a", "time")
    await ledger.finish("turn-a", ended_by="ask_customer", reply_kind="clarify")

    await ledger.start(turn_id="turn-b", user_id=str(user.id), route="orchestrate",
                       rule="R5", asked_at=t_minus_1)
    await ledger.set_ask("turn-b", "time")
    await ledger.finish("turn-b", ended_by="ask_customer", reply_kind="clarify")

    ctx = await load_turn_context(test_db, user, turn_id="t3", asked_at=asked_at)

    assert ctx.open_question is not None
    assert ctx.open_question.field == "time"
    assert ctx.open_question.times == 2


async def test_no_open_question_when_last_turn_reply_kind_is_not_clarify(test_db):
    user = await _a_user(test_db)
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    await ledger.start(turn_id="prev", user_id=str(user.id), route="orchestrate",
                       rule="R5", asked_at=asked_at - _timedelta(minutes=2))
    await ledger.set_ask("prev", "time")
    await ledger.finish("prev", ended_by="finish", reply_kind="answer")

    ctx = await load_turn_context(test_db, user, turn_id="t2", asked_at=asked_at)

    assert ctx.open_question is None


async def test_unannounced_is_loaded_from_ledger(test_db):
    user = await _a_user(test_db)
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    await ledger.start(turn_id="prev", user_id=str(user.id), route="orchestrate",
                       rule="R2", asked_at=asked_at - _timedelta(minutes=1))
    await ledger.set_pending_outcome("prev", PendingOutcome(
        outcome="approved", proposal_id="507f1f77bcf86cd799439011",
        kind="book", spoken="3 giờ chiều mai",
    ))
    await ledger.finish("prev", ended_by="forced", reply_kind=None)

    ctx = await load_turn_context(test_db, user, turn_id="t2", asked_at=asked_at)

    assert ctx.unannounced is not None
    assert ctx.unannounced.proposal_id == "507f1f77bcf86cd799439011"
    assert ctx.unannounced.outcome == "approved"


async def test_customer_ref_uses_address_phrase_and_staff_note(test_db):
    user = await _a_user(test_db, phone="0923456789", name="Cô Mai")
    await test_db["users"].update_one({"_id": user.id}, {"$set": {"staff_note": "khách quen"}})
    user = user.model_copy(update={"staff_note": "khách quen"})

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert ctx.customer.id == str(user.id)
    assert "Mai" in ctx.customer.address_phrase or ctx.customer.address_phrase == "anh chị"
    assert ctx.customer.staff_note == "khách quen"


async def test_goal_defaults_when_state_has_none(test_db):
    user = await _a_user(test_db)

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert ctx.goal == GoalProgress()


async def test_digest_text_rendered_from_state(test_db):
    user = await _a_user(test_db)
    state_service = ConversationStateService(test_db)
    await state_service.record_goal_progress(str(user.id), GoalProgress(goal_id="g1"))
    await test_db["conversations"].update_one(
        {"user_id": str(user.id)},
        {"$set": {"state.digest": {"wants": ["cắt tóc"], "settled": [], "declined": [],
                                   "constraints": [], "open": []}}},
    )

    ctx = await load_turn_context(test_db, user, turn_id="t1", asked_at=now_utc())

    assert "cắt tóc" in ctx.digest_text
    assert ctx.goal.goal_id == "g1"
