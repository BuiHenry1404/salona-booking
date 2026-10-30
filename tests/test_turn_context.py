"""Task 1.2: `TurnContext` và `render_context` hai mức (lite/full)."""
from __future__ import annotations

import inspect
import re
from datetime import timedelta

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
    render_context,
)
from app.agents.booking.schemas import Moment
from app.core.clock import now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.models.conversation import ConversationSlots, GoalProgress, QueuedRequest
from app.models.shop import ShopHours, ShopStatusView

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
