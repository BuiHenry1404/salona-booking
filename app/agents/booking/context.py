"""`TurnContext` và hai mức render (§4.2, Task 1.2).

`TurnContext` là bối cảnh một lượt, dựng bởi State Resolver (Task 1.4, chưa
làm ở đây). Mọi tên lớp/trường sao y từ thiết kế. `render_context` chỉ DỰNG
CHỮ — không gọi DB, không nhận chữ khách (tách khỏi `UNTRUSTED`, M2–M4 bọc
câu khách bằng hằng số này ở một chỗ khác, không phải ở đây).

Nhãn render tiếng Anh, giá trị tiếng Việt lấy từ `spoken`; mọi id/iso in ra
PHẢI đến từ một trường của `ctx` — không bịa chuỗi trông giống id.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.booking.schemas import Moment, QuestionField
from app.agents.booking_graph.context import (
    QUEUE_MAX_AGE_MINUTES,
    address_phrase,
    display_name,
    render_digest,
    render_slots,
)
from app.core.clock import to_local
from app.core.vi_format import _WEEKDAYS, format_vi_datetime
from app.models.conversation import ConversationSlots, GoalProgress, QueuedRequest
from app.models.shop import ShopHours, ShopStatusView
from app.models.user import User

# Nhãn DUY NHẤT bọc chữ khách trong mọi human message (M2–M4 import từ đây).
# `render_context` không nhận và không in chữ khách — hằng số này chỉ sống ở
# đây để chỗ dựng prompt dùng chung.
UNTRUSTED = "Customer said (untrusted):\n"

# `turn_id`/`asked_at` là sổ sách nội bộ (mốc cho ProposalService.approve,
# khoá ledger) — không có ý nghĩa với model, cố ý KHÔNG render.
NOT_RENDERED = frozenset({"turn_id", "asked_at"})


# ---------------------------------------------------------------------------
# Các lớp con của TurnContext
# ---------------------------------------------------------------------------


class CustomerRef(BaseModel):
    """Khách của lượt này. `address_phrase` là cách gọi ("chị Lan", "anh
    chị") đã suy sẵn — render KHÔNG BAO GIỜ tự đoán giới tính từ tên."""

    id: str
    display_name: str
    address_phrase: str
    staff_note: str | None = None


class AppointmentRef(BaseModel):
    id: str
    start_at: datetime
    spoken: str
    note: str | None = None


class PendingProposal(BaseModel):
    id: str
    kind: Literal["book", "move", "cancel"]
    start_at: datetime
    spoken: str
    note: str | None = None
    expires_at: datetime
    unclear_count: int
    target: AppointmentRef | None = None


class OpenQuestion(BaseModel):
    field: QuestionField
    asked_at: datetime
    times: int


class PreviousCall(BaseModel):
    """Một dòng rút từ ledger của lượt trước (§6)."""

    action: str
    status: Literal["ok", "needs_info", "failed", "rejected"]
    error_code: str | None = None
    offered: list[Moment] = []
    resolved: Moment | None = None
    proposal: PendingProposal | None = None


class PreviousTurn(BaseModel):
    turn_id: str
    route: str
    calls: list[PreviousCall] = []
    reply_kind: str
    ask_field: QuestionField | None = None


class UnannouncedOutcome(BaseModel):
    """Lượt trước ghi/huỷ xong mà khách chưa được báo (C1)."""

    proposal_id: str
    outcome: Literal["approved", "failed"]
    kind: str
    spoken: str
    error: str | None = None
    # Lượt (ledger) mang kết quả này — graph báo lại để events.py
    # `mark_announced` đúng lượt đó sau khi khách đã nghe (fix Task 5.3).
    # Sổ sách nội bộ, không render cho model.
    turn_id: str | None = None
    # Mốc giờ THẬT của lịch đã ghi (`PendingOutcome.appointment.at.iso`) —
    # guard đọc giờ của dòng R0 từ đây (fix round 2, Task 5.3). None khi lượt
    # trước không ghi được gì (`failed`).
    iso: str | None = None


class TurnContext(BaseModel):
    """Bối cảnh một lượt — §4.2 nguyên văn. Dựng bởi State Resolver (Task 1.4)."""

    turn_id: str
    asked_at: datetime  # mốc câu khách; = turn_at cho ProposalService.approve

    now_local: datetime
    today: date
    calendar_14d: list[tuple[date, str]]

    customer: CustomerRef
    shop_status: ShopStatusView
    shop_hours: ShopHours

    upcoming: list[AppointmentRef] = []  # ≤5
    recent_visits: list[date] = []

    pending: PendingProposal | None = None  # CHỐT lúc resolve; decide_pending luôn nhắm id này
    recently_declined: PendingProposal | None = None
    unannounced: UnannouncedOutcome | None = None  # §6

    queued_requests: list[QueuedRequest] = []
    open_question: OpenQuestion | None = None
    answers_open_question_hint: bool = False
    last_bot_text: str = ""

    previous_turns: list[PreviousTurn] = []  # ≤2 lượt trước trong ngày
    goal: GoalProgress

    slots: ConversationSlots | None = None
    digest_text: str = ""


Level = Literal["lite", "full"]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _moment_spoken(m: Moment | None) -> str:
    return m.spoken if m is not None else "-"


def _render_pending_short(label: str, p: PendingProposal) -> str:
    return f"{label}: {p.kind} {p.spoken} [id: {p.id}]"


def _render_pending_full(label: str, p: PendingProposal) -> str:
    line = (
        f"{label}: {p.kind} {p.spoken} [id: {p.id}] "
        f"(expires_at={p.expires_at.isoformat()}, unclear_count={p.unclear_count})"
    )
    if p.note:
        line += f" note={p.note}"
    if p.target is not None:
        line += f" target=[id: {p.target.id}] {p.target.spoken}"
    return line


def _render_appointment_full(a: AppointmentRef) -> str:
    line = f"- {a.spoken} [id: {a.id}] [{a.start_at.isoformat()}]"
    if a.note:
        line += f" note={a.note}"
    return line


def _render_queued_full(q: QueuedRequest) -> str:
    line = f"- {q.spoken} [id: {q.queue_id}]"
    if q.note:
        line += f" note={q.note}"
    return line


def _render_previous_call(c: PreviousCall) -> str:
    offered = ",".join(m.spoken for m in c.offered)
    resolved = _moment_spoken(c.resolved)
    proposal = c.proposal.id if c.proposal is not None else "-"
    return f"    {c.action} {c.status} offered=[{offered}] resolved={resolved} proposal={proposal}"


def _render_previous_turn(t: PreviousTurn) -> list[str]:
    lines = [f"- turn {t.turn_id} route={t.route} reply_kind={t.reply_kind} ask_field={t.ask_field}"]
    lines.extend(_render_previous_call(c) for c in t.calls)
    return lines


def _render_goal_lite(g: GoalProgress) -> str:
    return f"Goal: goal_id={g.goal_id} turns_without_progress={g.turns_without_progress}"


def _render_goal_full(g: GoalProgress) -> str:
    return (
        f"Goal: goal_id={g.goal_id} turns_without_progress={g.turns_without_progress} "
        f"last_marker={g.last_marker} service={g.service} appointment_id={g.appointment_id}"
    )


def _render_unannounced(u: UnannouncedOutcome) -> str:
    line = f"Unannounced outcome: {u.kind} {u.spoken} -> {u.outcome} [id: {u.proposal_id}]"
    if u.error:
        line += f" error={u.error}"
    return line


def render_context(ctx: TurnContext, level: Level) -> str:
    """Dựng khối bối cảnh văn bản cho một lượt. Không nhận, không in chữ khách."""
    lines: list[str] = []

    if level == "full" and ctx.unannounced is not None:
        lines.append(_render_unannounced(ctx.unannounced))

    lines.append(f"Now: {format_vi_datetime(ctx.now_local)}")
    if level == "lite":
        lines.append(f"Customer: {ctx.customer.address_phrase}")
    else:
        lines.append(f"Customer: {ctx.customer.address_phrase} ({ctx.customer.display_name})")

    if ctx.pending is not None:
        lines.append(_render_pending_short("Pending proposal", ctx.pending))
    else:
        lines.append("Pending proposal: none")

    if ctx.open_question is not None:
        lines.append(f"Open question: {ctx.open_question.field}")
    else:
        lines.append("Open question: none")

    if level == "lite":
        lines.append(f"Queued requests: {len(ctx.queued_requests)}")
        lines.append(_render_goal_lite(ctx.goal))
        return "\n".join(lines)

    # --- mức full: thêm mọi trường còn lại ---
    lines.append(f"Today: {ctx.today.isoformat()}")
    lines.append(
        "Calendar (14 days): "
        + ", ".join(f"{label} {d.isoformat()}" for d, label in ctx.calendar_14d)
    )
    lines.append(f"Customer id: [id: {ctx.customer.id}]")
    if ctx.customer.staff_note:
        lines.append(f"Staff note: {ctx.customer.staff_note}")

    lines.append(
        f"Shop: busy={ctx.shop_status.is_busy} hours={ctx.shop_hours.open_time}-{ctx.shop_hours.close_time}"
    )

    if ctx.pending is not None:
        lines.append(_render_pending_full("Pending proposal detail", ctx.pending))
    if ctx.recently_declined is not None:
        lines.append(_render_pending_full("Recently declined", ctx.recently_declined))

    lines.append("Upcoming appointments:")
    lines.extend(_render_appointment_full(a) for a in ctx.upcoming)

    if ctx.recent_visits:
        lines.append("Recent visits: " + ", ".join(d.isoformat() for d in ctx.recent_visits))

    lines.append("Queued requests:")
    lines.extend(_render_queued_full(q) for q in ctx.queued_requests)

    if ctx.open_question is not None:
        lines.append(
            f"Open question detail: field={ctx.open_question.field} "
            f"times={ctx.open_question.times} asked_at={ctx.open_question.asked_at.isoformat()}"
        )
    lines.append(f"Answers open question hint: {ctx.answers_open_question_hint}")
    lines.append(f"Last bot text: {ctx.last_bot_text}")

    lines.append("Previous turns:")
    for t in ctx.previous_turns:
        lines.extend(_render_previous_turn(t))

    lines.append(_render_goal_full(ctx.goal))

    if ctx.digest_text:
        lines.append(f"Digest: {ctx.digest_text}")

    slots_block = render_slots(ctx.slots)
    if slots_block:
        lines.append(f"State slots: {slots_block}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Nạp `TurnContext` từ DB (Task 1.4, §4.2/§6)
# ---------------------------------------------------------------------------

UPCOMING_LIMIT = 5
RECENT_VISITS_LIMIT = 3
# Đóng trong vòng ngần này phút mới còn tính là "vừa từ chối" — cùng hằng số
# với `PROPOSAL_MAX_AGE_MINUTES` (một đề xuất hết hạn cũng coi như bị từ chối
# trong đúng khoảng thời gian nó còn "mới" với khách).
RECENTLY_DECLINED_MAX_AGE_MINUTES = 10
_DECLINED_STATUSES = frozenset({"rejected", "superseded", "expired"})


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _appointment_ref(appointment) -> AppointmentRef:
    return AppointmentRef(
        id=str(appointment.id),
        start_at=appointment.start_at,
        spoken=format_vi_datetime(appointment.start_at),
        note=appointment.note,
    )


def _pending_proposal(proposal) -> PendingProposal:
    """`PendingProposal` từ một `Proposal` DB — `target` (nếu move/cancel)
    dựng từ `target_snapshot` (lịch đích LÚC ĐỀ XUẤT, không đọc lại DB)."""
    target = None
    if proposal.target_snapshot is not None and proposal.appointment_id is not None:
        target = AppointmentRef(
            id=proposal.appointment_id,
            start_at=proposal.target_snapshot.start_at,
            spoken=format_vi_datetime(proposal.target_snapshot.start_at),
        )
    return PendingProposal(
        id=str(proposal.id),
        kind=proposal.kind,
        start_at=proposal.start_at,
        spoken=format_vi_datetime(proposal.start_at),
        note=proposal.note,
        expires_at=proposal.expires_at,
        unclear_count=proposal.unclear_count,
        target=target,
    )


def _calendar_14d(today: date) -> list[tuple[date, str]]:
    items = []
    for offset in range(14):
        d = today + timedelta(days=offset)
        items.append((d, f"{_WEEKDAYS[d.weekday()]} {d.day}/{d.month}"))
    return items


def _live_queued(queued: list[QueuedRequest], now: datetime) -> list[QueuedRequest]:
    """Các mục hàng chờ còn hiệu lực: giờ chưa qua, chưa quá
    `QUEUE_MAX_AGE_MINUTES` kể từ lúc xếp hàng (như `live_queue` cũ, nhưng
    đọc thẳng từ `QueuedRequest` đã gõ kiểu thay vì dict thô)."""
    out = []
    for item in queued:
        start = _aware(item.start_at)
        queued_at = _aware(item.queued_at)
        if start > now and now - queued_at <= timedelta(minutes=QUEUE_MAX_AGE_MINUTES):
            out.append(item)
    return out


def _open_question(previous_turns: list["PreviousTurn"], asked_at: datetime) -> OpenQuestion | None:
    """Câu hỏi còn mở, suy từ lượt NGAY TRƯỚC (mới nhất): chỉ khi lượt đó kết
    thúc bằng `reply_kind == "clarify"` và có `ask_field`. `times` = số lượt
    LIÊN TIẾP gần nhất (tính từ lượt mới nhất lùi về) cùng hỏi field đó —
    `previous_turns` chỉ mang tối đa 2 lượt nên `times` chỉ có thể là 1 hay 2.
    """
    if not previous_turns:
        return None
    latest = previous_turns[0]
    if latest.ask_field is None or latest.reply_kind != "clarify":
        return None
    times = 1
    if len(previous_turns) > 1 and previous_turns[1].ask_field == latest.ask_field:
        times = 2
    return OpenQuestion(field=latest.ask_field, asked_at=asked_at, times=times)


async def load_turn_context(
    db: AsyncIOMotorDatabase, user: User, *, turn_id: str, asked_at: datetime
) -> TurnContext:
    """Dựng `TurnContext` của một lượt — nạp song song, không gọi LLM.

    Import cục bộ `TurnLedger`/các service: `ledger.py` import ngược lại các
    lớp con của `TurnContext` ở module này, nhập ở đầu file sẽ vòng lặp.
    """
    from app.agents.booking.ledger import TurnLedger
    from app.services.appointment import AppointmentService
    from app.services.conversation import ConversationService
    from app.services.proposal import ProposalService
    from app.services.shop import ShopService

    user_id = str(user.id)
    now_local = to_local(asked_at)
    today = now_local.date()

    conversations = ConversationService(db)
    appointments = AppointmentService(db)
    ledger = TurnLedger(db)

    async def _window():
        state = await conversations.get_state(user_id)
        after = state.covers_until if state else None
        history = await conversations.history(user_id, after=after)
        return state, history

    (
        shop_status,
        shop_hours,
        upcoming,
        recent_visits_appointments,
        latest_proposal,
        (state, history),
        previous_turns,
        unannounced,
    ) = await asyncio.gather(
        ShopService(db).get_status(),
        ShopService(db).get_hours(),
        appointments.upcoming_for(user),
        appointments.recent_visits(user, limit=RECENT_VISITS_LIMIT),
        ProposalService(db).latest(user),
        _window(),
        ledger.previous_turns(user_id, today=today),
        ledger.unannounced(user_id, today=today),
    )

    pending = None
    recently_declined = None
    if latest_proposal is not None:
        if latest_proposal.status == "pending" and _aware(latest_proposal.expires_at) > asked_at:
            pending = _pending_proposal(latest_proposal)
        elif latest_proposal.status in _DECLINED_STATUSES:
            closed = _aware(latest_proposal.updated_at)
            if asked_at - closed <= timedelta(minutes=RECENTLY_DECLINED_MAX_AGE_MINUTES):
                recently_declined = _pending_proposal(latest_proposal)

    last_bot_text = ""
    if history and history[-1].role == "assistant":
        last_bot_text = history[-1].content

    goal = state.goal if state and state.goal is not None else GoalProgress()
    queued = _live_queued(state.queued, asked_at) if state else []
    slots = state.slots if state else None
    digest_text = render_digest(state.digest, state.customer_quotes) if state else ""

    return TurnContext(
        turn_id=turn_id,
        asked_at=asked_at,
        now_local=now_local,
        today=today,
        calendar_14d=_calendar_14d(today),
        customer=CustomerRef(
            id=user_id,
            display_name=display_name(user.full_name),
            address_phrase=address_phrase(user),
            staff_note=user.staff_note,
        ),
        shop_status=shop_status,
        shop_hours=shop_hours,
        upcoming=[_appointment_ref(a) for a in upcoming[:UPCOMING_LIMIT]],
        recent_visits=[to_local(a.start_at).date() for a in recent_visits_appointments],
        pending=pending,
        recently_declined=recently_declined,
        unannounced=unannounced,
        queued_requests=queued,
        open_question=_open_question(previous_turns, asked_at),
        answers_open_question_hint=False,
        last_bot_text=last_bot_text,
        previous_turns=previous_turns,
        goal=goal,
        slots=slots,
        digest_text=digest_text,
    )
