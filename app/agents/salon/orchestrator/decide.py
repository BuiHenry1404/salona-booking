"""Decision Gate + cổng ghi của gói `salon` (Task 8, docs/architech-agent.md luật 1/3).

`veto()` là nơi CODE có tiếng nói cuối cùng trên nhãn Orchestrator gửi lên
(`decide_pending`): chỉ được HẠ cấp nhãn hoặc từ chối, không bao giờ nâng —
dò giờ/ngày/thứ trong câu khách bằng `shared.text_scan` (0 regex, 0 từ điển ý
định), không đọc hiểu câu khách bằng model. `apply_decision` là ĐƯỜNG GHI DUY
NHẤT cho cả nút bấm lẫn chữ gõ sau khi qua `veto()` — module DUY NHẤT của gói
`salon` được phép gọi `ProposalService.approve` (`tests/test_write_fence.py`).

Viết mới hoàn toàn cho gói `salon` (gói agent cũ đã xoá ở Task 16; chỉ đọc
`decide.py` của nó để biết cách transaction cũ được mở, không chép nguyên văn).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List, Literal, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.salon.shared.schemas import Moment, PendingOutcome, PendingView
from app.agents.salon.shared.text_scan import scan_day_offsets, scan_times, scan_weekdays
from app.agents.salon.shared.timeparse import _to_24h
from app.agents.salon.turn.ledger import TurnLedger
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.text import strip_diacritics
from app.core.vi_format import format_vi_datetime
from app.models.proposal import Proposal
from app.models.user import User
from app.services.appointment import AppointmentService, send_notices
from app.services.proposal import ApprovalFailed, ApprovalResult, ProposalService

logger = get_logger(__name__)

DecideLabel = Literal["approve", "reject", "change", "unclear", "none"]
Aside = Literal["relative_free", "defer_only", "defer_with_request"]

# Quét cả ngày để tìm 3 mốc gần nhất (giống hằng số WHOLE_DAY của gói cũ) —
# `find_free_slots` không có nghĩa "cả ngày" riêng, chỉ `limit` lớn hơn số mốc
# tối đa trong một ngày mở cửa.
_WHOLE_DAY_SLOTS = 200
_NEAREST_COUNT = 3

# Báo tin chủ tiệm chạy nền sau khi transaction duyệt đã commit (shield). Giữ
# tham chiếu mạnh: event loop chỉ giữ weak-ref, task không ai giữ có thể bị GC
# giữa chừng (cùng mẫu `_INFLIGHT_APPROVALS` của `app/services/proposal.py`).
_INFLIGHT_NOTICES: set = set()


class Veto(BaseModel):
    """Kết quả của `veto()` — CODE quyết định, không phải model."""

    label: DecideLabel
    reason: Optional[str] = None
    reject_code: Optional[str] = None


# ---------------------------------------------------------------------------
# veto() — chỉ hạ cấp nhãn, dò giờ/ngày/thứ bằng text_scan
# ---------------------------------------------------------------------------


def _hour_differs(raw_hour: int, period: Optional[str], minute: Optional[int],
                  target: datetime) -> bool:
    # `scan_times` trả buổi CÓ dấu ("tối"); `_to_24h` (bảng `_PART_OF_DAY_LABEL`)
    # chỉ nhận khoá KHÔNG dấu ("toi") — bỏ dấu trước khi tra, không thì mọi giờ
    # có buổi đều rơi vào nhánh "không chắc buổi" và so sai bằng mod-12 (lỗi
    # review round 1, Task 8: "9 giờ tối" lẫn với "9 giờ sáng").
    bare_period = strip_diacritics(period) if period else None
    resolved = _to_24h(raw_hour, bare_period) if bare_period else None
    if resolved is not None:
        if resolved != target.hour:
            return True
    elif raw_hour % 12 != target.hour % 12:
        return True
    return minute is not None and minute != target.minute


def _mentions_other_time(question: str, target: datetime, now: datetime) -> bool:
    """Câu khách nêu một giờ/ngày/thứ KHÁC `target` (giờ Việt Nam)."""
    for hour, period, minute in scan_times(question):
        if _hour_differs(hour, period, minute, target):
            return True
    for offset in scan_day_offsets(question):
        if now.date() + timedelta(days=offset) != target.date():
            return True
    for weekday in scan_weekdays(question):
        if weekday != target.weekday():
            return True
    return False


def _day_matches(offset: int, target: datetime, now: datetime) -> bool:
    return now.date() + timedelta(days=offset) == target.date()


def _other_time(question: str, pending_at: datetime, protected: Sequence[datetime],
                now: datetime) -> bool:
    """∃ một mốc khách nêu (giờ, HOẶC ngày tương đối, HOẶC thứ — xét riêng
    từng loại) khác `pending_at` MÀ CŨNG khác MỌI mốc trong `protected`.

    Mỗi mốc khách nêu được xét RIÊNG (không gộp "có mốc nào khác" của cả
    câu rồi mới so protected — review round 1, Task 8 #2): một câu nói hai
    giờ, một giờ khớp đề xuất và giờ kia khớp một lịch đang `protected`,
    không được tính là "nói giờ khác" — mỗi giờ đã có chỗ của nó."""
    for hour, period, minute in scan_times(question):
        if not _hour_differs(hour, period, minute, pending_at):
            continue
        if any(not _hour_differs(hour, period, minute, p) for p in protected):
            continue
        return True
    for offset in scan_day_offsets(question):
        if _day_matches(offset, pending_at, now):
            continue
        if any(_day_matches(offset, p, now) for p in protected):
            continue
        return True
    for weekday in scan_weekdays(question):
        if weekday == pending_at.weekday():
            continue
        if any(weekday == p.weekday() for p in protected):
            continue
        return True
    return False


def _restates_proposal_time(question: str, pending_at: datetime, now: datetime) -> bool:
    """Khách CÓ nêu giờ và giờ đó trùng khớp `pending_at` (không giờ nào
    khác) — câu chỉ lặp lại đúng giờ đang chờ, không phải đổi ý."""
    return bool(scan_times(question)) and not _mentions_other_time(question, pending_at, now)


def veto(
    label: DecideLabel,
    aside: Optional[Aside],
    question: str,
    pending: PendingView,
    now: datetime,
    protected: Sequence[datetime] = (),
) -> Veto:
    """Chỉ HẠ cấp nhãn model gửi lên, không bao giờ nâng (docs/architech-agent.md
    luật 3, CONTEXT.md quyết định 17). Thứ tự luật:

    1. `approve` mà câu khách nêu giờ/ngày khác đề xuất VÀ khác mọi giờ trong
       `protected` -> `change`.
    2. `change` mà câu khách chỉ nhắc lại đúng giờ đề xuất -> từ chối
       `same_time_as_pending` (nhãn giữ nguyên `change`, chỉ gắn `reject_code`
       cho caller tự quyết có hỏi lại hay không).
    3. `approve` + `aside == "relative_free"` -> `unclear`.
    4. `approve` + `aside` hoãn (`defer_only`/`defer_with_request`) -> `none`.
    5. Còn lại: giữ nguyên nhãn model gửi lên.
    """
    pending_at = to_local(datetime.fromisoformat(pending.at.iso))
    protected_local = [to_local(p) for p in protected]
    now_local = to_local(now)

    if label == "approve" and _other_time(question, pending_at, protected_local, now_local):
        return Veto(label="change", reason="other_time")
    if label == "change" and _restates_proposal_time(question, pending_at, now_local):
        return Veto(label=label, reject_code="same_time_as_pending")
    if label == "approve" and aside == "relative_free":
        return Veto(label="unclear", reason="relative_free")
    if label == "approve" and aside in ("defer_only", "defer_with_request"):
        return Veto(label="none", reason="defer")
    return Veto(label=label)


# ---------------------------------------------------------------------------
# apply_decision() — cổng ghi duy nhất, một transaction cho ghi lịch + ledger
# ---------------------------------------------------------------------------


def _outcome(outcome: str, pending: PendingView, *, appointment_id: Optional[str] = None,
            error: Optional[str] = None, reason: Optional[str] = None,
            nearest: Optional[List[Moment]] = None) -> PendingOutcome:
    return PendingOutcome(
        outcome=outcome,  # type: ignore[arg-type]
        proposal_id=pending.id,
        kind=pending.kind,
        at=pending.at,
        appointment_id=appointment_id,
        error=error,
        reason=reason,
        nearest=nearest or [],
        service_ids=list(pending.service_ids),
    )


async def _nearest_free_moments(db: AsyncIOMotorDatabase, proposal: Proposal) -> List[Moment]:
    """3 mốc còn trống GẦN giờ đề xuất nhất, cùng ngày — dùng khi `approve`
    thất bại vì giờ vừa bị người khác lấy mất."""
    ignore_id = proposal.appointment_id if proposal.kind in ("move", "cancel") else None
    free = await AppointmentService(db).find_free_slots(
        to_local(proposal.start_at).date(), limit=_WHOLE_DAY_SLOTS,
        ignore_appointment_id=ignore_id, duration_minutes=proposal.duration_minutes,
    )
    nearest = sorted(sorted(free, key=lambda s: abs(s - proposal.start_at))[:_NEAREST_COUNT])
    return [Moment(iso=to_local(s).isoformat(), spoken=format_vi_datetime(s)) for s in nearest]


async def _apply_approved(result: ApprovalResult, pending: PendingView) -> PendingOutcome:
    appointment_id = str(result.appointment.id) if result.appointment is not None else None
    return _outcome("approved", pending, appointment_id=appointment_id)


async def _current_state_outcome(
    db: AsyncIOMotorDatabase, user: User, pending: PendingView, session
) -> PendingOutcome:
    """Đề xuất không còn `pending` (bị đóng ở nơi khác đúng lúc này — nút bấm,
    lượt khác) khi `reject`/`supersede` trả `False` hay `mark_unclear` trả
    `None`. Đọc lại TRẠNG THÁI THẬT trong CÙNG session thay vì ghi liều nhãn
    của `label` (review round 1, Task 8 #3): đã `approved` thì báo `approved`
    với đúng lịch đã ghi, không phải `rejected`/`superseded` bịa; còn lại báo
    `not_approvable` với `reason` là trạng thái thật."""
    current = await ProposalService(db).get(user, pending.id, session=session)
    if current is None:
        return _outcome("not_approvable", pending, reason="not_found")
    if current.status == "approved":
        appointment = await AppointmentService(db).repo.get_by_id(
            current.result_appointment_id or "", session=session
        )
        appointment_id = str(appointment.id) if appointment is not None else current.result_appointment_id
        return _outcome("approved", pending, appointment_id=appointment_id)
    return _outcome("not_approvable", pending, reason=current.status)


async def _apply_unclear(db: AsyncIOMotorDatabase, user: User, pending: PendingView,
                         session) -> PendingOutcome:
    proposal = await ProposalService(db).mark_unclear(user, pending.id, session=session)
    if proposal is None:
        return await _current_state_outcome(db, user, pending, session)
    if proposal.status == "rejected":
        return _outcome("rejected", pending)
    return _outcome("clarify", pending)


async def apply_decision(
    db: AsyncIOMotorDatabase, user: User, *, pending: PendingView, label: DecideLabel,
    turn_id: str, asked_at: datetime, ledger: TurnLedger, thread_id: Optional[str] = None,
) -> PendingOutcome:
    """Ghi lịch (nếu `approve`) và quyết định (`salon_turns`) trong CÙNG MỘT
    transaction Mongo (CONTEXT.md quyết định 4): cả hai cùng có hoặc cùng
    không — lượt bị huỷ giữa chừng không để lại nửa vời.

    `label` đã qua `veto()` — `reject_code` của nó bị chặn TRƯỚC khi gọi đây
    (caller không bao giờ truyền label của một veto bị từ chối).

    `thread_id`: chỉ để GHI LOG có cấu trúc (`user_id`/`turn_id`/`thread_id`
    luôn đi cùng nhau — Global Constraints); không ảnh hưởng quyết định ghi.
    Task 11's tool biết `thread_id` của lượt nên truyền vào; không có thì log
    thiếu trường đó, không chặn đường ghi.
    """
    log_extra = {"user_id": str(user.id), "turn_id": turn_id, "thread_id": thread_id}

    if label == "none":
        # Không đụng đề xuất — một lệnh ghi ledger duy nhất, tự nó đã nguyên tử.
        outcome = _outcome("kept", pending)
        await ledger.set_decision(turn_id, outcome)
        return outcome

    notices: List = []

    async def _write(session) -> PendingOutcome:
        # `with_transaction` có thể chạy lại cả callback (TransientTransactionError)
        # — báo tin của lần bị abort phải bỏ đi.
        notices.clear()
        if label == "approve":
            result = await ProposalService(db).approve(
                user, pending.id, turn_at=asked_at, session=session
            )
            notices.extend(result.notices)
            if result.outcome == "approved":
                outcome = await _apply_approved(result, pending)
            else:
                outcome = _outcome("not_approvable", pending, reason=result.reason)
        elif label == "reject":
            flipped = await ProposalService(db).reject(user, pending.id, session=session)
            outcome = (_outcome("rejected", pending) if flipped
                      else await _current_state_outcome(db, user, pending, session))
        elif label == "change":
            flipped = await ProposalService(db).supersede(user, pending.id, session=session)
            outcome = (_outcome("superseded", pending) if flipped
                      else await _current_state_outcome(db, user, pending, session))
        else:  # "unclear"
            outcome = await _apply_unclear(db, user, pending, session)
        await ledger.set_decision(turn_id, outcome, session=session)
        return outcome

    try:
        async with await db.client.start_session() as session:
            outcome = await session.with_transaction(_write)
    except ApprovalFailed as exc:
        return await _record_failure(
            db, user, pending, exc.result, turn_id=turn_id, ledger=ledger, log_extra=log_extra,
        )

    if notices:
        # Lịch đã COMMIT — gửi sau, ngoài transaction (app/services/appointment.py::send_notices).
        await _send_notices(notices, log_extra=log_extra)
    return outcome


async def _record_failure(
    db: AsyncIOMotorDatabase, user: User, pending: PendingView, result: ApprovalResult, *,
    turn_id: str, ledger: TurnLedger, log_extra: dict,
) -> PendingOutcome:
    """Transaction duyệt đã abort sạch (`ApprovalFailed`, vd giờ vừa bị người
    khác lấy) — đọc 3 mốc trống gần nhất NGOÀI transaction, rồi ghi `failed` +
    quyết định ledger trong một transaction RIÊNG."""
    error = result.error or ""
    proposal = result.proposal
    nearest = await _nearest_free_moments(db, proposal) if proposal is not None else []
    outcome = _outcome("failed", pending, error=result.error, nearest=nearest)

    async def _write(session) -> PendingOutcome:
        flipped = await ProposalService(db).mark_failed(user, pending.id, error, session=session)
        if not flipped:
            logger.warning(
                "approve_failed_not_recorded",
                extra={**log_extra, "proposal_id": pending.id},
            )
        await ledger.set_decision(turn_id, outcome, session=session)
        return outcome

    async with await db.client.start_session() as session:
        return await session.with_transaction(_write)


async def _send_notices(notices: list, *, log_extra: dict) -> None:
    """`await` trong `asyncio.shield` — lượt chat bị huỷ (lượt mới thay) đúng
    lúc đang gửi thì việc gửi VẪN chạy xong; lỗi chỉ log, không làm hỏng câu
    trả lời cho khách (lịch đã ghi thật rồi)."""
    task = asyncio.ensure_future(send_notices(notices))
    _INFLIGHT_NOTICES.add(task)
    task.add_done_callback(_INFLIGHT_NOTICES.discard)
    task.add_done_callback(lambda t: _log_notice_failure(t, log_extra=log_extra))
    await asyncio.shield(task)


def _log_notice_failure(task: asyncio.Future, *, log_extra: dict) -> None:
    if task.cancelled() or task.exception() is None:
        return
    logger.warning(
        "owner_notice_failed", extra={**log_extra, "error": type(task.exception()).__name__},
    )
