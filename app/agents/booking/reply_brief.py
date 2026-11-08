"""`build_reply_brief` — dựng `ReplyBrief` từ kết quả một lượt (Task 4.1,
§5.4 của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Conversation Agent (Task 4.2) là agent DUY NHẤT nói với khách; nó chỉ nhận
`ReplyBrief` này + cửa sổ lịch sử — module này KHÔNG gọi LLM, chỉ đọc lại
những gì `OrchestrateOutcome`/`ButtonOutcome` đã ghi và suy ra sự thật để
Conversation Agent không được bịa. Task 4.4b (quyết định chủ dự án 06/11): bỏ
`classify_only`/`ClassifyOutcome` — `social`/`unsupported` giờ đến thẳng từ
`finish(kind_hint=...)` của Orchestrator, cùng đường với `answer`.

`facts_for(record, ctx, now)` ứng với ĐÚNG MỘT dòng bảng ánh xạ §5.4 — nó chỉ
đọc `record.result` (một `TaskResult` của `appointment_task`/`shop_info_task`,
hoặc một `PendingOutcome` của `decide_pending`), không biết gì về các
`CallRecord` khác trong lượt; `ctx`/`now` chỉ dùng cho hai dòng cần dữ kiện
KHÔNG nằm trong `TaskResult`/`PendingOutcome` (`ctx.shop_hours` cho
`outside_hours`/`closed_day`/`time_passed`, `now` để tính phút còn lại của
`PendingOutcome{kept}.expires_at`). `build_reply_brief` gộp `facts_for` của
mọi `CallRecord.allowed` lại, rồi thêm các dòng không gắn với MỘT call cụ thể
(`unannounced` R0, `recently_declined`, `shop_phone` của `unsupported`,
`error_reason`/cấm mọi claim của `limit_reached`)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.agents.booking.context import TurnContext, UnannouncedOutcome
from app.agents.booking.orchestrator.agent import OrchestrateOutcome
from app.agents.booking.schemas import (
    AppointmentList,
    CallRecord,
    ClarifyRequest,
    Fact,
    FreeSlots,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ReplyBrief,
    ReplyKind,
    ResolvedTime,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
)
from app.agents.booking.middleware import FALLBACK_ANSWER
from app.agents.booking.phrase import code_phrase
from app.core.clock import ensure_aware
from app.core.config import settings

# ---------------------------------------------------------------------------
# Câu cứng pin sẵn (nguyên văn từ confirm.py cũ, xem brief Task 4.1)
# ---------------------------------------------------------------------------

FAILED_FALLBACK = "Dạ {address}, {when} em chưa ghi được, {address} nhắn lại giờ khác giúp em nhé."
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé."
PROPOSAL_FALLBACK = "Dạ {address}, em giữ chỗ {when} cho {address} nhé, {address} đồng ý không ạ?"
UNANNOUNCED_LINE = {"approved": "đã {verb} xong {when}", "failed": "{when} em chưa ghi được"}

# `kind` (book/move/cancel) -> động từ nói với khách và -> claim bị cấm/được
# dùng cho `code_phrase` (tên khoá của `_TEMPLATES` trong `phrase.py`).
_VERB_FOR_KIND = {"book": "đặt", "move": "dời", "cancel": "huỷ"}
_CLAIM_FOR_KIND = {"book": "booked", "move": "moved", "cancel": "cancelled"}
_ALL_CLAIMS: list[Literal["booked", "cancelled", "moved"]] = ["booked", "cancelled", "moved"]

# Khoá `Fact` mang mốc giờ ĐANG/ĐÃ giữ thật (không phải mọi fact có `iso` —
# ví dụ `slot`/`queued_at` chỉ là giờ trống). `guard.py` import hằng số này
# thay vì chép lại danh sách khoá — một nguồn sự thật duy nhất cho tên khoá.
HELD_FACT_KEYS = frozenset({"held_at", "will_cancel", "booked_at", "moved_to", "cancelled_at"})

_HOURS_ERROR_CODES = frozenset({"outside_hours", "closed_day", "time_passed"})


def unannounced_line(unannounced: UnannouncedOutcome) -> str:
    """Câu R0 bắt buộc (`must_say`) cho kết quả lượt trước khách chưa được
    báo — graph dùng lại đúng câu này để biết câu đáp cuối đã báo hay chưa."""
    if unannounced.outcome == "approved":
        verb = _VERB_FOR_KIND.get(unannounced.kind, "")
        return UNANNOUNCED_LINE["approved"].format(verb=verb, when=unannounced.spoken)
    return UNANNOUNCED_LINE["failed"].format(when=unannounced.spoken)


class ButtonOutcome(BaseModel):
    """Kết quả đường nút (Đồng ý/Không), Task 4.5 — không đi qua Orchestrator."""

    kind: Literal["approved", "rejected", "failed", "not_approvable", "stale"]
    outcome: PendingOutcome | None


TurnOutcome = OrchestrateOutcome | ButtonOutcome


# ---------------------------------------------------------------------------
# facts_for — MỘT dòng bảng §5.4 ứng với MỘT CallRecord
# ---------------------------------------------------------------------------


def _facts_for_proposal(data: ProposalCreated) -> tuple[list[Fact], list[str], list[str]]:
    facts: list[Fact] = []
    must_say: list[str] = []
    if data.kind == "cancel":
        facts.append(Fact(key="will_cancel", spoken=data.start_at.spoken, iso=data.start_at.iso))
        must_say.append(data.start_at.spoken)
    else:
        facts.append(Fact(key="held_at", spoken=data.start_at.spoken, iso=data.start_at.iso))
        must_say.append(data.start_at.spoken)
        if data.kind == "move" and data.replaces is not None:
            facts.append(Fact(key="replaces", spoken=data.replaces.spoken, iso=data.replaces.iso))
    if data.note:
        facts.append(Fact(key="note", text=data.note))
    for queued in data.queued:
        facts.append(Fact(key="queued_at", spoken=queued.spoken, iso=queued.iso))
    return facts, must_say, [_CLAIM_FOR_KIND[data.kind]]


def _facts_for_free_slots(data: FreeSlots) -> tuple[list[Fact], list[str], list[str]]:
    facts = [Fact(key="slot", spoken=m.spoken, iso=m.iso) for m in data.slots]
    facts.append(Fact(key="day", text=data.day.isoformat()))
    facts.append(Fact(key="more", text=str(data.more)))
    return facts, [m.spoken for m in data.slots], []


def _facts_for_appointment_list(data: AppointmentList) -> tuple[list[Fact], list[str], list[str]]:
    facts = [Fact(key="appointment", spoken=a.at.spoken, iso=a.at.iso, text=a.note) for a in data.appointments]
    return facts, [a.at.spoken for a in data.appointments], []


def _facts_for_shop_hours(data: ShopHoursOut) -> tuple[list[Fact], list[str], list[str]]:
    # Giờ mở/đóng cửa KHÔNG phải mốc thời gian (Shop Info trả `iso` dạng
    # "08:00", M6b L1) — để ở `text`, không bao giờ vào `iso` cho guard parse.
    facts = [
        Fact(key="open", spoken=data.open.spoken, text=data.open.iso),
        Fact(key="close", spoken=data.close.spoken, text=data.close.iso),
    ]
    return facts, [data.open.spoken, data.close.spoken], []


def _facts_for_shop_status(data: ShopStatusOut) -> tuple[list[Fact], list[str], list[str]]:
    facts = [Fact(key="busy", text=str(data.busy))]
    must_say: list[str] = []
    if data.until is not None:
        facts.append(Fact(key="until", spoken=data.until.spoken, iso=data.until.iso))
        must_say.append(data.until.spoken)
    return facts, must_say, []


def _facts_for_shop_open(data: ShopOpenOut) -> tuple[list[Fact], list[str], list[str]]:
    facts = [
        Fact(key="at", spoken=data.at.spoken, iso=data.at.iso),
        Fact(key="open", text=str(data.open)),
        Fact(key="reason", text=data.reason),
    ]
    must_say = [data.at.spoken]
    if data.next_change is not None:
        facts.append(Fact(key="next_change", spoken=data.next_change.spoken, iso=data.next_change.iso))
        must_say.append(data.next_change.spoken)
    return facts, must_say, []


def _facts_for_failed_task(result: TaskResult, ctx: TurnContext) -> tuple[list[Fact], list[str], list[str]]:
    code = result.error_code
    if code == "slot_unavailable":
        facts: list[Fact] = []
        must_say: list[str] = []
        if result.asked is not None:
            facts.append(Fact(key="asked", spoken=result.asked.spoken, iso=result.asked.iso))
            must_say.append(result.asked.spoken)
        facts.extend(Fact(key="nearest", spoken=m.spoken, iso=m.iso) for m in result.nearest)
        return facts, must_say, []
    if code in _HOURS_ERROR_CODES:
        # Fix round 1 (ruling 3): `TaskResult` của lỗi này không mang giờ mở
        # cửa — khối bối cảnh (`ctx.shop_hours`) là nguồn thật DUY NHẤT, không
        # đổi schema `TaskResult` cho việc này.
        facts = [
            Fact(key="reason", text=code),
            Fact(key="open", text=ctx.shop_hours.open_time),
            Fact(key="close", text=ctx.shop_hours.close_time),
        ]
        must_say = []
        if result.asked is not None:
            facts.append(Fact(key="asked", spoken=result.asked.spoken, iso=result.asked.iso))
            must_say.append(result.asked.spoken)
        return facts, must_say, []
    if code == "already_yours":
        facts = []
        must_say = []
        if result.asked is not None:
            facts.append(Fact(key="existing", spoken=result.asked.spoken, iso=result.asked.iso))
            must_say.append(result.asked.spoken)
        return facts, must_say, []
    # "failed khác" (schema_invalid, model_error, tool_timeout, fact_mismatch,
    # appointment_not_found...) — dòng chung của bảng §5.4: chỉ `error_reason`,
    # cấm mọi claim vì không biết việc có xảy ra hay không.
    return [Fact(key="error_reason", text=code or "unknown")], [], list(_ALL_CLAIMS)


def _facts_for_task_result(result: TaskResult, ctx: TurnContext) -> tuple[list[Fact], list[str], list[str]]:
    data = result.data
    if isinstance(data, ProposalCreated):
        return _facts_for_proposal(data)
    if isinstance(data, FreeSlots):
        return _facts_for_free_slots(data)
    if isinstance(data, AppointmentList):
        return _facts_for_appointment_list(data)
    if isinstance(data, ShopHoursOut):
        return _facts_for_shop_hours(data)
    if isinstance(data, ShopStatusOut):
        return _facts_for_shop_status(data)
    if isinstance(data, ShopOpenOut):
        return _facts_for_shop_open(data)
    if isinstance(data, ResolvedTime):
        # §5.4: `ResolvedTime{missing}` không tự sinh fact/claim — câu hỏi
        # (`ask`) đến từ `ask_customer` của Orchestrator, không phải ở đây.
        return [], [], []
    if result.status == "failed":
        return _facts_for_failed_task(result, ctx)
    return [], [], []


def _minutes_left(expires_at: datetime, now: datetime) -> int:
    delta = ensure_aware(expires_at) - ensure_aware(now)
    return max(0, round(delta.total_seconds() / 60))


def _facts_for_pending(pending: PendingOutcome, now: datetime) -> tuple[list[Fact], list[str], list[str]]:
    claim = _CLAIM_FOR_KIND.get(pending.kind)
    claims = [claim] if claim else []

    if pending.outcome == "approved":
        key = {"book": "booked_at", "move": "moved_to", "cancel": "cancelled_at"}.get(pending.kind, "booked_at")
        iso = pending.appointment.at.iso if pending.appointment is not None else None
        return [Fact(key=key, spoken=pending.spoken, iso=iso)], [pending.spoken], []

    if pending.outcome in ("rejected", "superseded"):
        return [Fact(key="declined", text=pending.kind, spoken=pending.spoken)], [], claims

    if pending.outcome == "clarify":
        return [Fact(key="pending", text=pending.kind, spoken=pending.spoken)], [pending.spoken], claims

    if pending.outcome == "kept":
        facts = [Fact(key="pending", text=pending.kind, spoken=pending.spoken)]
        if pending.expires_at is not None:
            # Fix round 1 (ruling 2): `expires_at` tồn tại trên `PendingOutcome`
            # đúng cho dòng này (§5.4 "kept{kind, spoken, expires_in}") — dòng
            # cũ bỏ sót nó.
            facts.append(Fact(
                key="expires_in",
                iso=pending.expires_at.isoformat(),
                text=str(_minutes_left(pending.expires_at, now)),
            ))
        return facts, [], claims

    if pending.outcome == "failed":
        facts = [Fact(key="asked", spoken=pending.spoken)]
        reason_text = pending.error or pending.reason
        if reason_text:
            facts.append(Fact(key="error_reason", text=reason_text))
        facts.extend(Fact(key="nearest", spoken=m.spoken, iso=m.iso) for m in pending.nearest)
        return facts, [pending.spoken], list(_ALL_CLAIMS)

    if pending.outcome == "not_approvable":
        # Review M4 finding (binding, xem brief task): `reason` có thể là
        # "expired"/"approving"/"not_found" hay một status đề xuất khác đã bị
        # đóng ở nơi khác ("rejected"/"superseded"/"approved" — ví dụ nút bấm
        # vừa xử lý xong). Bảng §5.4 chỉ có MỘT dòng cho `not_approvable`,
        # không phân nhánh theo giá trị `reason` — áp y nguyên cho mọi giá
        # trị. `reason="approved"` là trường hợp bảng không cho hành vi hợp
        # lý (cấm `booked` khi lịch THẬT ra đã được ghi ở nơi khác) — xem
        # ghi chú concern trong báo cáo task, KHÔNG tự chế một nhánh riêng
        # ở đây để tránh hành vi không có trong spec.
        if pending.reason == "expired":
            return [Fact(key="expired", spoken=pending.spoken)], [], list(_ALL_CLAIMS)
        reason_text = pending.reason or pending.error or "not_approvable"
        return [Fact(key="error_reason", text=reason_text)], [], list(_ALL_CLAIMS)

    return [], [], []


def facts_for(
    record: CallRecord, ctx: TurnContext, now: datetime
) -> tuple[list[Fact], list[str], list[str]]:
    """`(facts, must_say, must_not_claim)` cho MỘT dòng bảng §5.4 — đọc
    đúng `record.result`, không biết gì về các `CallRecord` khác. `ctx`/`now`
    chỉ dùng cho hai dòng cần dữ kiện ngoài `record` (xem docstring module)."""
    result = record.result
    if isinstance(result, PendingOutcome):
        return _facts_for_pending(result, now)
    if isinstance(result, TaskResult):
        return _facts_for_task_result(result, ctx)
    return [], [], []


# ---------------------------------------------------------------------------
# reply_kind_for — luật chọn kind (§5.4, thứ tự cố định)
# ---------------------------------------------------------------------------


def _is_new_proposal_call(record: CallRecord) -> bool:
    return (
        record.allowed
        and isinstance(record.result, TaskResult)
        and isinstance(record.result.data, ProposalCreated)
    )


def _unpack(outcome: TurnOutcome) -> tuple[PendingOutcome | None, list[CallRecord], object | None, bool]:
    """`(pending_outcome, calls, ask, có ProposalCreated mới)` — chung cho
    `reply_kind_for` và `build_reply_brief`, bất kể `outcome` tới từ
    Orchestrator hay nút bấm."""
    if isinstance(outcome, ButtonOutcome):
        return outcome.outcome, [], None, False
    calls = outcome.calls
    has_new_proposal = any(_is_new_proposal_call(c) for c in calls)
    return outcome.pending_outcome, calls, outcome.ask, has_new_proposal


def reply_kind_for(outcome: TurnOutcome, ctx: TurnContext) -> ReplyKind:
    if isinstance(outcome, ButtonOutcome) and outcome.kind == "stale":
        return "stale_button"

    pending, _calls, ask, has_new_proposal = _unpack(outcome)

    if pending is not None:
        if pending.outcome == "approved":
            return "confirmed"
        if pending.outcome in ("failed", "not_approvable"):
            return "failed"
        if pending.outcome in ("rejected", "superseded") and not has_new_proposal:
            return "declined"

    if has_new_proposal:
        return "proposal_made"

    if ask is not None or (pending is not None and pending.outcome == "clarify"):
        return "clarify"

    if isinstance(outcome, OrchestrateOutcome):
        if outcome.ended_by in ("limit", "forced", "error"):
            if outcome.ended_by == "error" or outcome.rounds_executed >= settings.orch_max_tool_rounds:
                return "limit_reached"
            return "answer"
        if outcome.finish is not None:
            return outcome.finish.kind_hint

    return "answer"


# ---------------------------------------------------------------------------
# build_reply_brief
# ---------------------------------------------------------------------------


def _find_new_proposal(calls: list[CallRecord]) -> ProposalCreated | None:
    for record in calls:
        if _is_new_proposal_call(record):
            return record.result.data
    return None


def _then_line(calls: list[CallRecord]) -> str | None:
    proposal = _find_new_proposal(calls)
    if proposal is not None and proposal.queued:
        return f"Sau giờ này em sẽ hỏi tiếp {proposal.queued[0].spoken}"
    return None


def _tone_context(
    ctx: TurnContext, pending: PendingOutcome | None, has_new_proposal: bool
) -> Literal["first_time", "returning", "changed_time", "after_decline"]:
    declined_no_new = pending is not None and pending.outcome in ("rejected", "superseded") and not has_new_proposal
    if ctx.recently_declined is not None or declined_no_new:
        return "after_decline"
    if pending is not None and pending.outcome == "superseded" and has_new_proposal:
        return "changed_time"
    if ctx.recent_visits:
        return "returning"
    return "first_time"


def _fallback_for(
    kind: ReplyKind,
    ctx: TurnContext,
    pending: PendingOutcome | None,
    calls: list[CallRecord],
    tone_context: str,
    previous_reply: str | None,
) -> str:
    address = ctx.customer.address_phrase
    if kind == "confirmed" and pending is not None:
        fact = {
            "kind": _CLAIM_FOR_KIND.get(pending.kind, pending.kind),
            "address": address,
            "when": pending.spoken,
            "context": tone_context,
        }
        previous = [previous_reply] if previous_reply else []
        return code_phrase(fact, previous)
    if kind == "failed" and pending is not None:
        return FAILED_FALLBACK.format(address=address, when=pending.spoken)
    if kind == "proposal_made":
        proposal = _find_new_proposal(calls)
        when = proposal.start_at.spoken if proposal is not None else ""
        return PROPOSAL_FALLBACK.format(address=address, when=when)
    if kind == "stale_button":
        return BUTTON_STALE_REPLY.replace("anh chị", address)
    return FALLBACK_ANSWER


def _is_free_slots(record: CallRecord) -> bool:
    return isinstance(record.result, TaskResult) and isinstance(record.result.data, FreeSlots)


def build_reply_brief(
    ctx: TurnContext, outcome: TurnOutcome, *, previous_reply: str | None, now: datetime
) -> ReplyBrief:
    kind = reply_kind_for(outcome, ctx)
    pending, calls, ask, has_new_proposal = _unpack(outcome)

    facts: list[Fact] = []
    must_say: list[str] = []
    must_not_claim: list[str] = []

    def _add_claims(new_claims: list[str]) -> None:
        for claim in new_claims:
            if claim not in must_not_claim:
                must_not_claim.append(claim)

    announced_line: str | None = None
    # R0 — câu chưa báo của lượt trước, luôn đứng ĐẦU `must_say`.
    if ctx.unannounced is not None:
        unannounced = ctx.unannounced
        facts.append(Fact(
            key="prior_outcome", text=unannounced.outcome, spoken=unannounced.spoken, iso=unannounced.iso,
        ))
        announced_line = unannounced_line(unannounced)
        must_say.append(announced_line)

    if isinstance(outcome, OrchestrateOutcome):
        for record in outcome.calls:
            if not record.allowed:
                continue
            f, say, claim = facts_for(record, ctx, now)
            facts.extend(f)
            # M6b L4: lượt đã có đề xuất mới thì giờ trống (`FreeSlots`) chỉ là
            # Fact (model được nhắc), không bắt đọc hết — `must_say` chỉ còn
            # `held_at` (và R0). Lượt chỉ `find_slots` giữ nguyên bảng §5.4.
            if kind == "proposal_made" and _is_free_slots(record):
                continue
            must_say.extend(say)
            _add_claims(claim)
    elif isinstance(outcome, ButtonOutcome) and outcome.outcome is not None:
        f, say, claim = _facts_for_pending(outcome.outcome, now)
        facts.extend(f)
        must_say.extend(say)
        _add_claims(claim)

    has_appointment_task_call = isinstance(outcome, OrchestrateOutcome) and any(
        c.allowed and c.tool == "appointment_task" for c in outcome.calls
    )
    if ctx.recently_declined is not None and has_appointment_task_call:
        declined = ctx.recently_declined
        facts.append(Fact(key="declined", text="chưa ghi gì", spoken=declined.spoken))
        claim = _CLAIM_FOR_KIND.get(declined.kind)
        if claim:
            _add_claims([claim])

    if kind == "unsupported":
        facts.append(Fact(key="shop_phone", text=settings.shop_phone))

    if kind == "limit_reached" and isinstance(outcome, OrchestrateOutcome):
        # Fix round 1 (ruling 1): §5.4 "failed khác / limit" cấm MỌI claim ở
        # mức lượt, không chỉ claim của từng call — một lượt limit chỉ có một
        # `find_slots` thành công (không claim gì) không được để
        # `must_not_claim` rỗng, Conversation Agent vẫn có thể bịa "đã đặt".
        facts.append(Fact(key="error_reason", text=outcome.ended_by))
        _add_claims(list(_ALL_CLAIMS))

    tone_context = _tone_context(ctx, pending, has_new_proposal)

    ask_out: ClarifyRequest | None = None
    if ask is not None:
        ask_out = ClarifyRequest(field=ask.field, hint=ask.hint)
    elif pending is not None and pending.outcome == "clarify":
        ask_out = ClarifyRequest(field="confirm", hint=None)

    fallback = _fallback_for(kind, ctx, pending, calls, tone_context, previous_reply)
    must_say = list(dict.fromkeys(must_say))  # khử trùng lặp, giữ thứ tự (M6b L4)

    return ReplyBrief(
        kind=kind,
        address=ctx.customer.address_phrase,
        facts=facts,
        ask=ask_out,
        must_say=must_say,
        must_not_claim=must_not_claim,
        then=_then_line(calls),
        previous_reply=previous_reply,
        tone_context=tone_context,
        digest_text=ctx.digest_text,
        staff_note=ctx.customer.staff_note,
        now_local=now,
        fallback=fallback,
        announced_line=announced_line,
    )
