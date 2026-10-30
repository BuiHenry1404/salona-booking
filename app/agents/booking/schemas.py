"""Hợp đồng vào/ra cho Orchestrator và 2 agent con (M2–M6).

Mọi tên lớp/trường sao y từ thiết kế `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`
(§5.1, §5.4). Luật strict-safe (§12.17, Task 0.6): model trở thành tool
argument (`TimeHint`, `ExtraRequest`, `AppointmentArgs`, `ShopArgs`,
`DecideArgs`, `AskCustomerArgs`, `FinishArgs`) không có field nào mang
default — tuỳ chọn biểu diễn bằng `X | None`, không dùng `conint`/`Field(ge=,
le=)` — để mọi field bắt buộc có mặt trong JSON Schema (Azure strict mode từ
chối `default`/`minimum`/`maximum`). Kiểm khoảng giá trị xảy ra sau, trong
middleware, không ở đây.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Sequence

from pydantic import BaseModel, create_model

# ---------------------------------------------------------------------------
# Literal dùng xuyên suốt
# ---------------------------------------------------------------------------

AppointmentAction = Literal[
    "resolve_time", "find_slots", "propose_book", "propose_move", "propose_cancel", "list_appointments"
]
ShopAction = Literal["get_hours", "get_status", "is_open_at"]
QuestionField = Literal["day", "time", "part_of_day", "service", "which_appointment", "week", "confirm"]
DecideLabel = Literal["approve", "reject", "change", "unclear", "none"]
Aside = Literal["relative_free", "defer_only", "defer_with_request"]
Missing = Literal["ngày nào", "giờ cụ thể", "sáng hay chiều", "tuần này hay tuần sau", "lịch nào"]
ReplyKind = Literal[
    "social", "unsupported", "answer", "clarify", "proposal_made", "confirmed", "declined",
    "stale_button", "failed", "limit_reached",
]
EndedBy = Literal["finish", "ask_customer", "forced", "limit", "error"]


# ---------------------------------------------------------------------------
# Mốc thời gian dùng chung
# ---------------------------------------------------------------------------


class Moment(BaseModel):
    """Một thời điểm đã chốt: ISO máy đọc + câu nói tiếng Việt."""

    iso: str
    spoken: str


# ---------------------------------------------------------------------------
# Tool argument — tool-facing, strict-safe (KHÔNG default, §5.1)
# ---------------------------------------------------------------------------


class TimeHint(BaseModel):
    """Gợi ý thời gian do Orchestrator điền. `anchor` thắng neo ngầm của parse_time."""

    day: date | None
    weekday: int | None
    day_offset: int | None
    hour: int | None
    minute: int | None
    part_of_day: Literal["sang", "trua", "chieu", "toi"] | None
    ambiguous_week: bool | None
    is_now: bool | None
    anchor: Literal["pending", "declined", "appointment", "queued"] | None
    queue_id: str | None  # bắt buộc khi anchor="queued"


class ExtraRequest(BaseModel):
    """Một yêu cầu đi kèm trong `also` (chỉ `propose_book`)."""

    time: TimeHint
    service: str | None
    for_whom: str | None


class AppointmentArgs(BaseModel):
    """Bản tĩnh. Bản động (giới hạn `appointment_id`/`queue_id`) xem `appointment_args_model`."""

    time: TimeHint | None
    service: str | None
    appointment_id: str | None  # ∈ TurnContext.upcoming — Literal động ở bản dựng runtime
    for_whom: str | None
    also: list[ExtraRequest] | None  # chỉ propose_book


class ShopArgs(BaseModel):
    time: TimeHint | None


class DecideArgs(BaseModel):
    label: DecideLabel
    aside: Aside | None
    reason: str  # nội bộ, trích cụm chữ khách làm căn cứ, để log soi


class AskCustomerArgs(BaseModel):
    field: QuestionField
    hint: str | None


class FinishArgs(BaseModel):
    kind_hint: Literal["answer", "unsupported", "social"]
    service: str | None
    stated: list[str]


def time_hint_model(queue_ids: Sequence[str]) -> type[BaseModel]:
    """Dựng `TimeHint` động: `queue_id` giới hạn theo `queue_ids` (Literal); rỗng → chỉ nhận None.

    Không bao giờ dựng `Literal[()]` — khi `queue_ids` rỗng, kiểu là `None` thẳng
    (trường vẫn bắt buộc có mặt, không default).
    """
    queue_id_type: type = type(None)
    if queue_ids:
        queue_id_type = Literal[tuple(queue_ids)] | None  # type: ignore[valid-type]

    return create_model(
        "TimeHint",
        __base__=BaseModel,
        day=(date | None, ...),
        weekday=(int | None, ...),
        day_offset=(int | None, ...),
        hour=(int | None, ...),
        minute=(int | None, ...),
        part_of_day=(Literal["sang", "trua", "chieu", "toi"] | None, ...),
        ambiguous_week=(bool | None, ...),
        is_now=(bool | None, ...),
        anchor=(Literal["pending", "declined", "appointment", "queued"] | None, ...),
        queue_id=(queue_id_type, ...),
    )


def appointment_args_model(appointment_ids: Sequence[str], queue_ids: Sequence[str]) -> type[BaseModel]:
    """Dựng `AppointmentArgs` động cho một lượt: `appointment_id` ∈ `appointment_ids`
    (Literal động; rỗng → chỉ nhận `None`, không `Literal[()]`), `TimeHint.queue_id`
    giới hạn theo `queue_ids` qua `time_hint_model` nội bộ.
    """
    dynamic_time_hint = time_hint_model(queue_ids)

    appointment_id_type: type = type(None)
    if appointment_ids:
        appointment_id_type = Literal[tuple(appointment_ids)] | None  # type: ignore[valid-type]

    dynamic_extra_request = create_model(
        "ExtraRequest",
        __base__=BaseModel,
        time=(dynamic_time_hint, ...),
        service=(str | None, ...),
        for_whom=(str | None, ...),
    )

    return create_model(
        "AppointmentArgs",
        __base__=BaseModel,
        time=(dynamic_time_hint | None, ...),
        service=(str | None, ...),
        appointment_id=(appointment_id_type, ...),
        for_whom=(str | None, ...),
        also=(list[dynamic_extra_request] | None, ...),
    )


# ---------------------------------------------------------------------------
# TaskSpec / TaskResult — hợp đồng code ↔ specialist
# ---------------------------------------------------------------------------


class TaskSpec(BaseModel):
    """Code dựng từ (tool, args); `task_id` sinh dạng "t<round>.<idx>"."""

    task_id: str
    agent: Literal["appointment", "shop_info"]
    action: AppointmentAction | ShopAction
    args: AppointmentArgs | ShopArgs


class ResolvedTime(BaseModel):
    start_at: Moment | None
    partial_date: date | None
    missing: list[Missing]
    day_from: str | None


class FreeSlots(BaseModel):
    day: date
    slots: list[Moment]
    more: bool
    fetched_at: datetime


class ProposalCreated(BaseModel):
    proposal_id: str
    kind: Literal["book", "move", "cancel"]
    start_at: Moment
    note: str | None
    replaces: Moment | None
    queued: list[Moment]


class AppointmentRefOut(BaseModel):
    id: str
    at: Moment
    note: str | None = None


class AppointmentList(BaseModel):
    appointments: list[AppointmentRefOut]


class ShopHoursOut(BaseModel):
    open: Moment
    close: Moment
    closed_days: list[str]


class ShopStatusOut(BaseModel):
    busy: bool
    until: Moment | None


class ShopOpenOut(BaseModel):
    at: Moment
    open: bool
    reason: Literal["closed_day", "before_open", "after_close", "owner_busy", "open"]
    next_change: Moment | None


TaskData = ResolvedTime | FreeSlots | ProposalCreated | AppointmentList | ShopHoursOut | ShopStatusOut | ShopOpenOut


class TaskResult(BaseModel):
    task_id: str
    agent: str
    action: str
    status: Literal["ok", "needs_info", "failed"]
    data: TaskData | None = None
    missing: list[Missing] = []
    error_code: str | None = None
    nearest: list[Moment] = []
    asked: Moment | None = None
    retryable: bool = False
    attempts: int = 1


class SpecialistOutput(BaseModel):
    """JSON mà AIMessage cuối của specialist PHẢI là (M2)."""

    status: Literal["ok", "needs_info", "failed"]
    data: TaskData | None = None
    missing: list[Missing] = []
    error_code: str | None = None


# ---------------------------------------------------------------------------
# PendingOutcome / CallRecord / Fact / ReplyBrief
# ---------------------------------------------------------------------------


class PendingOutcome(BaseModel):
    """§5.1."""

    outcome: Literal["approved", "rejected", "superseded", "clarify", "kept", "failed", "not_approvable"]
    proposal_id: str
    kind: str
    spoken: str
    appointment: AppointmentRefOut | None = None
    error: str | None = None
    reason: str | None = None
    nearest: list[Moment] = []
    expires_at: datetime | None = None  # cần cho nhánh "kept" trong §5.4 (đề xuất còn hạn)


class CallRecord(BaseModel):
    """§5.2 — một dòng trong ledger."""

    round: int
    index: int
    tool: str
    action: str | None
    args_norm: str
    allowed: bool
    reject_code: str | None = None
    result: TaskResult | PendingOutcome | dict | None = None
    markers: list[str] = []
    at: datetime


class Fact(BaseModel):
    key: str
    spoken: str | None = None
    iso: str | None = None
    text: str | None = None


class ClarifyRequest(BaseModel):
    field: QuestionField
    hint: str | None


class ReplyBrief(BaseModel):
    """§5.4 nguyên văn."""

    kind: ReplyKind
    address: str
    facts: list[Fact]
    ask: ClarifyRequest | None
    must_say: list[str]
    must_not_claim: list[Literal["booked", "cancelled", "moved"]]
    then: str | None
    previous_reply: str | None
    tone_context: Literal["first_time", "returning", "changed_time", "after_decline"]
    digest_text: str
    staff_note: str | None
    now_local: datetime
    fallback: str  # confirmed/failed: câu chốt CODE viết, không phải câu chung


class TurnFacts(BaseModel):
    service: str | None = None
    stated: list[str] = []
    answers_question: bool = False
