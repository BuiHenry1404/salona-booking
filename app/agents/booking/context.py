"""`TurnContext` và hai mức render (§4.2, Task 1.2).

`TurnContext` là bối cảnh một lượt, dựng bởi State Resolver (Task 1.4, chưa
làm ở đây). Mọi tên lớp/trường sao y từ thiết kế. `render_context` chỉ DỰNG
CHỮ — không gọi DB, không nhận chữ khách (tách khỏi `UNTRUSTED`, M2–M4 bọc
câu khách bằng hằng số này ở một chỗ khác, không phải ở đây).

Nhãn render tiếng Anh, giá trị tiếng Việt lấy từ `spoken`; mọi id/iso in ra
PHẢI đến từ một trường của `ctx` — không bịa chuỗi trông giống id.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.agents.booking.schemas import Moment, QuestionField
from app.agents.booking_graph.context import render_slots
from app.core.vi_format import format_vi_datetime
from app.models.conversation import ConversationSlots, GoalProgress, QueuedRequest
from app.models.shop import ShopHours, ShopStatusView

# Nhãn DUY NHẤT bọc chữ khách trong mọi human message (M2–M4 import từ đây).
# `render_context` không nhận và không in chữ khách — hằng số này chỉ sống ở
# đây để chỗ dựng prompt dùng chung.
UNTRUSTED = "Customer said (untrusted):\n"


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
    lines.append(f"Customer: {ctx.customer.address_phrase}")

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
