"""`build_reply_brief` — dựng `ReplyBrief` từ kết quả một lượt (Task 4.1,
§5.4 của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Conversation Agent (Task 4.2) là agent DUY NHẤT nói với khách; nó chỉ nhận
`ReplyBrief` này + cửa sổ lịch sử — module này KHÔNG gọi LLM, chỉ đọc lại
những gì `OrchestrateOutcome`/`ButtonOutcome` đã ghi và suy ra sự thật để
Conversation Agent không được bịa. Task 4.4b (quyết định chủ dự án 06/11): bỏ
`classify_only`/`ClassifyOutcome` — `social`/`unsupported` giờ đến thẳng từ
`finish(kind_hint=...)` của Orchestrator, cùng đường với `answer`.

`facts_for(record, ctx, now)` (một dòng bảng §5.4 cho một `CallRecord`) và các
câu mẫu đã dời sang `reply_check.py` (orchestrator-speaks, Task 2) — module
này import lại. `build_reply_brief` gộp `facts_for` của
mọi `CallRecord.allowed` lại, rồi thêm các dòng không gắn với MỘT call cụ thể
(`unannounced` R0, `recently_declined`, `shop_phone` của `unsupported`,
`error_reason`/cấm mọi claim của `limit_reached`)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.agents.booking.context import TurnContext
from app.agents.booking.orchestrator.agent import OrchestrateOutcome
from app.agents.booking.schemas import (
    CallRecord,
    ClarifyRequest,
    Fact,
    FreeSlots,
    PendingOutcome,
    ProposalCreated,
    ReplyBrief,
    ReplyKind,
    TaskResult,
)
from app.agents.booking.middleware import FALLBACK_ANSWER
from app.agents.booking.phrase import code_phrase
from app.core.config import settings

# Trích sự thật dùng chung + câu mẫu đã dời sang `reply_check.py`
# (orchestrator-speaks, Task 2) — import lại để graph cũ và test cũ chạy tới
# khi module này bị xoá.
from app.agents.booking.reply_check import (  # noqa: F401  (tái xuất cho graph/test cũ)
    BUTTON_STALE_REPLY,
    FAILED_FALLBACK,
    PROPOSAL_FALLBACK,
    _ALL_CLAIMS,
    _CLAIM_FOR_KIND,
    _facts_for_pending,
    _tone_context,
    facts_for,
    unannounced_line,
)

# Khoá `Fact` mang mốc giờ ĐANG/ĐÃ giữ thật (không phải mọi fact có `iso` —
# ví dụ `slot`/`queued_at` chỉ là giờ trống). `guard.py` import hằng số này
# thay vì chép lại danh sách khoá — một nguồn sự thật duy nhất cho tên khoá.
HELD_FACT_KEYS = frozenset({"held_at", "will_cancel", "booked_at", "moved_to", "cancelled_at"})


class ButtonOutcome(BaseModel):
    """Kết quả đường nút (Đồng ý/Không), Task 4.5 — không đi qua Orchestrator."""

    kind: Literal["approved", "rejected", "failed", "not_approvable", "stale"]
    outcome: PendingOutcome | None


TurnOutcome = OrchestrateOutcome | ButtonOutcome


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
