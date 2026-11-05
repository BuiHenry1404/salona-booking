"""`apply_decision` — thân thật của tool `decide_pending` (§5.5) và veto có
cấu trúc trên nhãn model đọc được (§9.3 (i)).

Đây là module DUY NHẤT ngoài `booking_graph/confirm.py` (gói cũ, M6 gỡ) được
phép gọi `ProposalService.approve` — xem `tests/test_write_fence.py`. Khách
xác nhận ở LƯỢT SAU; `decide_pending` là cách DUY NHẤT một lượt chat có thể
ghi lịch, và veto() là nơi CODE (không phải model) có tiếng nói cuối cùng
trên nhãn mà model gửi lên.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable, Sequence, TypeVar

from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import PendingProposal
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
from app.core.logging import get_logger
from app.core.vi_format import format_vi_datetime
from app.models.user import User
from app.services.appointment import AppointmentService, send_notices
from app.services.proposal import UNCLEAR_LIMIT, ApprovalFailed, ApprovalResult, ProposalService

if TYPE_CHECKING:  # tránh vòng import thật: tools.py import module này
    from app.agents.booking.orchestrator.tools import OrchestratorDeps

logger = get_logger(__name__)

# Báo tin chủ tiệm đang gửi nền sau commit (shield). Giữ tham chiếu mạnh: event
# loop chỉ giữ weak-ref, task không ai giữ có thể bị GC giữa chừng — cùng mẫu
# `_INFLIGHT_APPROVALS` của `app/services/proposal.py`.
_INFLIGHT_NOTICES: set = set()


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


T = TypeVar("T")


async def _in_transaction(db: AsyncIOMotorDatabase, write: Callable[..., Awaitable[T]]) -> T:
    """Chạy `write(session)` trong MỘT transaction Mongo (replica set một
    node, Task 3.6). `with_transaction` của Motor: lỗi thường -> abort rồi ném
    lại; lỗi mang nhãn TransientTransactionError -> chạy lại CẢ callback (nên
    `write` chỉ được gồm lệnh Mongo trong session — không báo tin, không đọc
    ngoài DB); huỷ task (CancelledError) giữa callback -> context của
    `start_transaction` abort. Không có `asyncio.shield`: huỷ lượt = không ghi gì."""
    async with await db.client.start_session() as session:
        return await session.with_transaction(write)


def _approved_outcome(pending: PendingProposal, result: ApprovalResult) -> PendingOutcome:
    if result.outcome == "failed":
        # `approve` có session báo lỗi nghiệp vụ bằng `ApprovalFailed`, không
        # trả "failed" — phòng vệ: đi đúng đường abort + transaction riêng.
        raise ApprovalFailed(result)
    if result.outcome == "not_approvable":
        return _outcome("not_approvable", pending, reason=result.reason)
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
    db: AsyncIOMotorDatabase, user: User, pending: PendingProposal, session
) -> PendingOutcome:
    proposals = ProposalService(db)
    proposal = await proposals.mark_unclear(user, pending.id, session=session)
    if proposal is None:
        # `mark_unclear` chỉ tăng được khi đề xuất CÒN pending — None nghĩa là
        # nó đã bị đóng ở đâu đó khác (nút bấm, lượt khác, hết hạn) ngay trước
        # khi `decide_pending` chạy tới đây. Tái dùng đúng quy ước `reason`
        # của `ProposalService._not_claimed` (chuỗi trạng thái thật, hoặc
        # "not_found"), không bịa "clarify" cho một đề xuất không còn đó.
        current = await proposals.get(user, pending.id, session=session)
        reason = current.status if current is not None else "not_found"
        return _outcome("not_approvable", pending, reason=reason)
    if proposal.unclear_count >= UNCLEAR_LIMIT:
        return _outcome("rejected", pending)
    return _outcome("clarify", pending)


async def apply_decision(deps: "OrchestratorDeps", label: DecideLabel) -> PendingOutcome:
    """§5.5 — nhãn ở đây đã qua `veto()` (`reject_code` đã bị chặn trước đó
    trong `decide_pending`, chưa bao giờ tới hàm này).

    Task 3.6 (quyết định chủ dự án 05/11, thay `asyncio.shield` của Fix round
    1): đổi đề xuất (kể cả ghi lịch của `approve`) và `pending_outcome` trong
    `turn_ledger` là MỘT transaction Mongo — cả hai cùng có hoặc cùng không,
    dù lượt bị huỷ (`asyncio.timeout` của `run_orchestrate`), server sập giữa
    chừng, hay một lệnh ghi lỗi. Lỗi ghi ledger nổi lên caller, không nuốt.

    Đường thất bại nghiệp vụ của `approve` (giờ vừa bị lấy -> DuplicateKeyError
    đã làm Mongo abort transaction): `ApprovalFailed` thoát ra, transaction 1
    abort (đề xuất về `pending`); `nearest` đọc NGOÀI transaction (không nằm
    trong callback có thể chạy lại); rồi transaction 2 ghi `failed` + outcome.
    Báo tin chủ tiệm (lịch mới/huỷ) chỉ gửi SAU commit."""
    db, user, ctx = deps.db, deps.user, deps.ctx
    pending = ctx.pending
    if pending is None:
        raise DecideRejected("no_pending")

    if label == "none":
        # Không đụng đề xuất — một lệnh ghi ledger duy nhất, tự nó đã nguyên tử.
        outcome = _outcome("kept", pending, expires_at=pending.expires_at)
        await deps.ledger.set_pending_outcome(deps.turn_id, outcome)
        return outcome

    notices: list = []

    async def _write(session) -> PendingOutcome:
        # Callback có thể chạy lại (TransientTransactionError): mỗi lần là một
        # `ProposalService` mới, báo tin của lần bị abort bỏ đi.
        notices.clear()
        if label == "approve":
            result = await ProposalService(db).approve(
                user, pending.id, turn_at=ctx.asked_at, session=session
            )
            notices.extend(result.notices)
            outcome = _approved_outcome(pending, result)
        elif label == "reject":
            await ProposalService(db).reject(user, pending.id, session=session)
            outcome = _outcome("rejected", pending)
        elif label == "change":
            await ProposalService(db).supersede(user, pending.id, session=session)
            outcome = _outcome("superseded", pending)
        else:  # "unclear"
            outcome = await _apply_unclear(db, user, pending, session)
        await deps.ledger.set_pending_outcome(deps.turn_id, outcome, session=session)
        return outcome

    try:
        outcome = await _in_transaction(db, _write)
    except ApprovalFailed as exc:
        return await _record_failure(deps, pending, exc.result)
    if notices:
        await _send_notices_shielded(list(notices), turn_id=deps.turn_id, user_id=str(user.id))
    return outcome


async def _send_notices_shielded(notices: list, *, turn_id: str, user_id: str) -> None:
    """Lịch đã COMMIT — báo tin chủ tiệm không được mất vì lượt bị huỷ
    (`asyncio.timeout` của `run_orchestrate`) đúng lúc đang gửi: chạy thành
    task riêng + `shield`; huỷ chỉ cắt chỗ chờ, task vẫn gửi xong."""
    task = asyncio.ensure_future(send_notices(notices))
    _INFLIGHT_NOTICES.add(task)
    task.add_done_callback(_INFLIGHT_NOTICES.discard)
    task.add_done_callback(lambda t: _log_notice_failure(t, turn_id=turn_id, user_id=user_id))
    await asyncio.shield(task)


def _log_notice_failure(task: asyncio.Future, *, turn_id: str, user_id: str) -> None:
    if task.cancelled() or task.exception() is None:
        return
    logger.warning(
        "owner_notice_failed",
        extra={"user_id": user_id, "turn_id": turn_id, "error": type(task.exception()).__name__},
    )


async def _record_failure(
    deps: "OrchestratorDeps", pending: PendingProposal, result: ApprovalResult
) -> PendingOutcome:
    """Transaction 2 của đường thất bại: transaction duyệt đã abort sạch."""
    db, user = deps.db, deps.user
    error = result.error or ""
    nearest = await _nearest_slots_around(db, pending)
    outcome = _outcome("failed", pending, error=result.error, nearest=nearest)

    async def _write(session) -> PendingOutcome:
        flipped = await ProposalService(db).mark_failed(user, pending.id, error, session=session)
        if not flipped:
            # Đề xuất đã bị đóng ở nơi khác giữa hai transaction (nút bấm, lượt
            # khác, hết hạn) — vẫn ghi outcome `failed` cho lượt này như cũ.
            logger.warning(
                "approve_failed_not_recorded",
                extra={"user_id": str(user.id), "turn_id": deps.turn_id,
                       "proposal_id": pending.id},
            )
        await deps.ledger.set_pending_outcome(deps.turn_id, outcome, session=session)
        return outcome

    return await _in_transaction(db, _write)
