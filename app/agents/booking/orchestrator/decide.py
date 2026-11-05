"""`apply_decision` — thân thật của tool `decide_pending` (§5.5) và veto có
cấu trúc trên nhãn model đọc được (§9.3 (i)).

Đây là module DUY NHẤT ngoài `booking_graph/confirm.py` (gói cũ, M6 gỡ) được
phép gọi `ProposalService.approve` — xem `tests/test_write_fence.py`. Khách
xác nhận ở LƯỢT SAU; `decide_pending` là cách DUY NHẤT một lượt chat có thể
ghi lịch, và veto() là nơi CODE (không phải model) có tiếng nói cuối cùng
trên nhãn mà model gửi lên.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Sequence

from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import PendingProposal, TurnContext
from app.agents.booking.schemas import (
    AppointmentRefOut,
    Aside,
    DecideLabel,
    Moment,
    PendingOutcome,
)
from app.agents.booking_graph.reply import other_time, restates_proposal_time
from app.agents.booking_graph.tools import WHOLE_DAY, nearest_slots
from app.core.clock import to_local
from app.core.vi_format import format_vi_datetime
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.proposal import UNCLEAR_LIMIT, ProposalService

if TYPE_CHECKING:  # tránh vòng import thật: tools.py import module này
    from app.agents.booking.orchestrator.tools import OrchestratorDeps


class DecideRejected(Exception):
    """Veto có cấu trúc (§9.3 (i)) từ chối hẳn lời gọi `decide_pending` — Task
    3.3's `OrchestratorGuard` bắt exception này và biến nó thành một
    `ToolMessage(status="error")`, không phải lỗi hệ thống."""

    def __init__(self, reject_code: str) -> None:
        super().__init__(reject_code)
        self.reject_code = reject_code


class Veto(BaseModel):
    """Kết quả của `veto()` — CODE quyết định, không phải model."""

    label: DecideLabel
    reason: str | None = None
    reject_code: str | None = None


def _as_other_time_mapping(pending: PendingProposal, queued: Sequence[datetime]) -> dict:
    """`other_time`/`restates_proposal_time` (gói cũ) nhận một Mapping có
    `start_at`/`queued` — `queued` cho `veto()` biết các giờ ĐANG CHỜ (lịch
    đặt lần lượt) để KHÔNG hiểu nhầm câu chỉ nói về lịch chờ ("ừ, còn lịch 2
    giờ thì để sau") thành khách đang đổi giờ đề xuất hiện tại."""
    return {"start_at": pending.start_at, "queued": list(queued)}


def veto(
    label: DecideLabel,
    aside: Aside | None,
    question: str,
    pending: PendingProposal,
    now: datetime,
    queued: Sequence[datetime] = (),
) -> Veto:
    """§9.3 (i) nguyên văn — chỉ HẠ cấp nhãn hoặc từ chối, không bao giờ nâng.

    Thứ tự luật (đúng bảng §5.5/§9.3):
    1. `approve` mà câu khách nêu giờ/ngày KHÁC đề xuất -> `change`.
    2. `change` mà câu khách NHẮC LẠI đúng giờ đề xuất (restate, không phải
       "không nêu giờ khác" nói chung — "thôi đổi lịch khác đi"/"lùi lại 30
       phút" không nêu giờ cụ thể nào nên KHÔNG bị từ chối, chỉ câu lặp lại
       đúng giờ đang chờ mới bị) -> từ chối `same_time_as_pending`.
    3. `approve` + `aside == "relative_free"` -> `unclear`.
    4. `approve` + `aside` hoãn (`defer_only`/`defer_with_request`) -> `none`.
    5. Còn lại: giữ nguyên nhãn model gửi lên.
    """
    mapping = _as_other_time_mapping(pending, queued)

    if label == "approve" and other_time(question, mapping, now):
        return Veto(label="change", reason="other_time")
    if label == "change" and restates_proposal_time(question, mapping, now):
        return Veto(label=label, reject_code="same_time_as_pending")
    if label == "approve" and aside == "relative_free":
        return Veto(label="unclear", reason="relative_free")
    if label == "approve" and aside in ("defer_only", "defer_with_request"):
        return Veto(label="none", reason="defer")
    return Veto(label=label)


def _outcome(
    outcome: str,
    pending: PendingProposal,
    *,
    appointment: AppointmentRefOut | None = None,
    error: str | None = None,
    reason: str | None = None,
    nearest: list[Moment] | None = None,
    expires_at: datetime | None = None,
) -> PendingOutcome:
    return PendingOutcome(
        outcome=outcome,  # type: ignore[arg-type]
        proposal_id=pending.id,
        kind=pending.kind,
        spoken=pending.spoken,
        appointment=appointment,
        error=error,
        reason=reason,
        nearest=nearest or [],
        expires_at=expires_at,
    )


async def _nearest_slots_around(db: AsyncIOMotorDatabase, pending: PendingProposal) -> list[Moment]:
    """3 mốc còn trống GẦN `pending.start_at` nhất, cùng ngày — dùng khi
    `approve` thất bại vì giờ vừa bị người khác lấy mất."""
    ignore_id = pending.target.id if pending.target is not None else None
    free = await AppointmentService(db).find_free_slots(
        to_local(pending.start_at).date(), limit=WHOLE_DAY, ignore_appointment_id=ignore_id,
    )
    nearest = nearest_slots(free, pending.start_at)
    return [Moment(iso=s.isoformat(), spoken=format_vi_datetime(s)) for s in nearest]


async def _apply_approve(
    db: AsyncIOMotorDatabase, user: User, ctx: TurnContext, pending: PendingProposal
) -> PendingOutcome:
    result = await ProposalService(db).approve(user, pending.id, turn_at=ctx.asked_at)

    if result.outcome == "not_approvable":
        return _outcome("not_approvable", pending, reason=result.reason)

    if result.outcome == "failed":
        nearest = await _nearest_slots_around(db, pending)
        return _outcome("failed", pending, error=result.error, nearest=nearest)

    appointment_ref = None
    if result.appointment is not None:
        appointment_ref = AppointmentRefOut(
            id=str(result.appointment.id),
            at=Moment(
                iso=result.appointment.start_at.isoformat(),
                spoken=format_vi_datetime(result.appointment.start_at),
            ),
            note=result.appointment.note,
        )
    return _outcome("approved", pending, appointment=appointment_ref)


async def _apply_unclear(
    db: AsyncIOMotorDatabase, user: User, pending: PendingProposal
) -> PendingOutcome:
    proposals = ProposalService(db)
    proposal = await proposals.mark_unclear(user, pending.id)
    if proposal is None:
        # `mark_unclear` chỉ tăng được khi đề xuất CÒN pending — None nghĩa là
        # nó đã bị đóng ở đâu đó khác (nút bấm, lượt khác, hết hạn) ngay trước
        # khi `decide_pending` chạy tới đây. Tái dùng đúng quy ước `reason`
        # của `ProposalService._not_claimed` (chuỗi trạng thái thật, hoặc
        # "not_found"), không bịa "clarify" cho một đề xuất không còn đó.
        current = await proposals.get(user, pending.id)
        reason = current.status if current is not None else "not_found"
        return _outcome("not_approvable", pending, reason=reason)
    if proposal.unclear_count >= UNCLEAR_LIMIT:
        return _outcome("rejected", pending)
    return _outcome("clarify", pending)


async def apply_decision(deps: "OrchestratorDeps", label: DecideLabel) -> PendingOutcome:
    """§5.5 — nhãn ở đây đã qua `veto()` (`reject_code` đã bị chặn trước đó
    trong `decide_pending`, chưa bao giờ tới hàm này)."""
    db, user, ctx = deps.db, deps.user, deps.ctx
    pending = ctx.pending
    if pending is None:
        raise DecideRejected("no_pending")

    if label == "approve":
        outcome = await _apply_approve(db, user, ctx, pending)
    elif label == "reject":
        await ProposalService(db).reject(user, pending.id)
        outcome = _outcome("rejected", pending)
    elif label == "change":
        await ProposalService(db).supersede(user, pending.id)
        outcome = _outcome("superseded", pending)
    elif label == "unclear":
        outcome = await _apply_unclear(db, user, pending)
    else:  # "none"
        outcome = _outcome("kept", pending, expires_at=pending.expires_at)

    await deps.ledger.set_pending_outcome(deps.turn_id, outcome)
    return outcome
