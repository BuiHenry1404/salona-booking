"""`reply_check.py` — sự thật của một lượt, phép kiểm câu trả lời và câu mẫu
code (§7 của `docs/superpowers/specs/2026-11-08-orchestrator-speaks-design.md`).

Orchestrator tự viết câu cho khách qua tool `reply`; module này KHÔNG gọi
LLM, chỉ dựng từ bối cảnh + các call được phép của lượt + quyết định đề xuất +
câu khách:

- `build_reply_facts` → `ReplyFacts`: mốc giờ có thật, giờ đang giữ thật, claim
  được phép, cụm bắt buộc nhắc, cờ "cần câu hỏi"/"cần số điện thoại", R0 và
  câu mẫu code của lượt;
- `check_reply_text` → mã vi phạm theo thứ tự cố định (`CHECK_CODES`);
- `is_announced` → câu đã báo R0 chưa.

Phần trích sự thật dùng chung (`facts_for` và các `_facts_for_*`, một dòng
bảng §5.4 của spec 2026-10-29 cho một `CallRecord`), `unannounced_line` và
các câu mẫu dời nguyên văn từ `reply_brief.py`; `reply_brief.py` import lại từ
đây cho graph cũ chạy tới khi bị xoá.

Không regex (chỉ `draft_checks.py` được `import re`)."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Iterable, Literal

from pydantic import BaseModel

from app.agents.booking import draft_checks, tokens
from app.agents.booking.context import TurnContext, UnannouncedOutcome
from app.agents.booking.phrase import code_phrase
from app.agents.booking.schemas import (
    AppointmentList,
    CallRecord,
    Fact,
    FreeSlots,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
)
from app.core.clock import ensure_aware, to_local
from app.core.config import settings
from app.core.vi_format import _clock_phrase, format_vi_hhmm

# ---------------------------------------------------------------------------
# Câu mẫu (dời nguyên văn từ reply_brief.py) + hai câu mẫu mới của §7
# ---------------------------------------------------------------------------

FAILED_FALLBACK = "Dạ {address}, {when} em chưa ghi được, {address} nhắn lại giờ khác giúp em nhé."
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé."
PROPOSAL_FALLBACK = "Dạ {address}, em giữ chỗ {when} cho {address} nhé, {address} đồng ý không ạ?"
UNANNOUNCED_LINE = {"approved": "đã {verb} xong {when}", "failed": "{when} em chưa ghi được"}
# §7: duyệt thất bại → nêu giờ, "chưa ghi được", số điện thoại tiệm (lượt DUY
# NHẤT được có số điện thoại). Câu chung không bao giờ mời gọi tiệm.
APPROVE_FAILED_FALLBACK = (
    "Dạ {address}, lịch {when} em chưa ghi được, {address} gọi giúp em số {phone} "
    "để tiệm xếp lịch trực tiếp nhé ạ."
)
GENERIC_FALLBACK = "Dạ {address} nói lại giúp em được không ạ?"

# `kind` (book/move/cancel) -> động từ nói với khách và -> claim.
_VERB_FOR_KIND = {"book": "đặt", "move": "dời", "cancel": "huỷ"}
_CLAIM_FOR_KIND = {"book": "booked", "move": "moved", "cancel": "cancelled"}
_ALL_CLAIMS: list[Literal["booked", "cancelled", "moved"]] = ["booked", "cancelled", "moved"]

_HOURS_ERROR_CODES = frozenset({"outside_hours", "closed_day", "time_passed"})


def unannounced_line(unannounced: UnannouncedOutcome) -> str:
    """Câu R0 bắt buộc (`must_say`) cho kết quả lượt trước khách chưa được
    báo — graph dùng lại đúng câu này để biết câu đáp cuối đã báo hay chưa."""
    if unannounced.outcome == "approved":
        verb = _VERB_FOR_KIND.get(unannounced.kind, "")
        return UNANNOUNCED_LINE["approved"].format(verb=verb, when=unannounced.spoken)
    return UNANNOUNCED_LINE["failed"].format(when=unannounced.spoken)


def normalize(text: str) -> str:
    """NFC + casefold + gộp khoảng trắng — không regex, chỉ `split`/`join`.

    Chữ Việt gõ dấu tổ hợp (NFD, vài bộ gõ/IME phát ra) và dấu dựng sẵn (NFC)
    KHÁC NHAU về byte dù hiển thị giống hệt — so sánh thẳng sẽ trật. (Dời từ
    `guard.py`, nơi giờ import lại hàm này — để ở guard thì vòng import
    guard → reply_brief → reply_check → guard.)"""
    return " ".join(unicodedata.normalize("NFC", text or "").casefold().split())


# ---------------------------------------------------------------------------
# facts_for — MỘT dòng bảng §5.4 ứng với MỘT CallRecord (dời từ reply_brief)
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
        # Review M4 finding (binding): `reason` có thể là "expired"/"approving"/
        # "not_found" hay một status đề xuất khác đã bị đóng ở nơi khác. Bảng
        # §5.4 chỉ có MỘT dòng cho `not_approvable`, không phân nhánh theo giá
        # trị `reason` — áp y nguyên cho mọi giá trị.
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
    chỉ dùng cho hai dòng cần dữ kiện ngoài `record` (`ctx.shop_hours` cho
    lỗi giờ mở cửa, `now` cho phút còn lại của `kept`)."""
    result = record.result
    if isinstance(result, PendingOutcome):
        return _facts_for_pending(result, now)
    if isinstance(result, TaskResult):
        return _facts_for_task_result(result, ctx)
    return [], [], []


def _tone_context(
    ctx: TurnContext, pending: PendingOutcome | None, has_new_proposal: bool
) -> Literal["first_time", "returning", "changed_time", "after_decline"]:
    """Giọng của câu chốt code (`code_phrase`)."""
    declined_no_new = pending is not None and pending.outcome in ("rejected", "superseded") and not has_new_proposal
    if ctx.recently_declined is not None or declined_no_new:
        return "after_decline"
    if pending is not None and pending.outcome == "superseded" and has_new_proposal:
        return "changed_time"
    if ctx.recent_visits:
        return "returning"
    return "first_time"


# ---------------------------------------------------------------------------
# ReplyFacts
# ---------------------------------------------------------------------------

ClockKey = tuple[int, int, str | None]
Claim = Literal["booked", "moved", "cancelled"]

# Thứ tự cố định của mã vi phạm `check_reply_text` trả về.
CHECK_CODES = ("claim", "unknown_time", "missing_fact", "missing_question", "missing_phone", "phone_not_allowed")

_PERIODS = ("sáng", "trưa", "chiều", "tối", "đêm")
# Duyệt thất bại = lượt DUY NHẤT phải (và được) có số điện thoại tiệm.
_APPROVE_FAILED = frozenset({"failed", "not_approvable"})
# Đề xuất đang chờ vẫn còn chờ sau quyết định của lượt.
_STILL_PENDING = frozenset({"kept", "clarify"})
# Động từ cam kết (`draft_checks.claim_verbs`) -> claim cần có. "đặt/ghi/chốt"
# cũng đúng sau một lần dời đã duyệt (lịch mới đã được ghi); "đổi" cũng đúng
# sau một lần đặt đã duyệt ("giờ đặt đã đổi thành …" — mẫu `changed_time` của
# `code_phrase`). Giờ trong mệnh đề vẫn phải là giờ đã ghi (`done_clocks`).
_CLAIMS_FOR_VERB: dict[str, frozenset[str]] = {
    "đặt": frozenset({"booked", "moved"}),
    "ghi": frozenset({"booked", "moved"}),
    "chốt": frozenset({"booked", "moved"}),
    "dời": frozenset({"moved"}),
    "đổi": frozenset({"moved", "booked"}),
    "hủy": frozenset({"cancelled"}),
    "huỷ": frozenset({"cancelled"}),
}


class ReplyFacts(BaseModel):
    clocks: set[ClockKey]           # mọi mốc giờ có thật của lượt
    held_clocks: set[ClockKey]      # giờ đang có đề xuất chờ thật (ctx.pending + đề xuất tạo lượt này)
    allowed_claims: set[Claim]
    must_mention: list[str]         # spoken bắt buộc: đề xuất mới, R0, kết quả duyệt
    question_required: bool         # lượt có ProposalCreated mới
    needs_phone: bool               # approve thất bại lượt này
    announce_turn_id: str | None
    announce_spoken: str | None
    fallback: str                   # câu mẫu code của lượt
    # Giờ của việc ĐÃ ghi thật (duyệt lượt này, R0 approved) — "đã đặt/dời/
    # huỷ <giờ>" chỉ hợp lệ với các giờ này, không ké thêm giờ khác.
    done_clocks: set[ClockKey] = set()


def _clock_of(dt: datetime) -> set[ClockKey]:
    local = to_local(ensure_aware(dt))
    return set(draft_checks.clock_keys(_clock_phrase(local.hour, local.minute)))


def _clocks_of_spoken(spoken: str | None) -> set[ClockKey]:
    return set(draft_checks.clock_keys(spoken or ""))


def _clocks_of_iso(iso: str | None) -> set[ClockKey]:
    if not iso:
        return set()
    try:
        return _clock_of(datetime.fromisoformat(iso))
    except ValueError:
        pass
    try:
        # Shop Info trả giờ mở/đóng cửa dạng "08:00", không phải datetime.
        return _clocks_of_spoken(format_vi_hhmm(iso))
    except ValueError:
        return set()


def _clocks_of_moment(moment: Moment | None) -> set[ClockKey]:
    if moment is None:
        return set()
    return _clocks_of_iso(moment.iso) | _clocks_of_spoken(moment.spoken)


def _moments_of_task(result: TaskResult) -> list[Moment]:
    moments: list[Moment] = []
    if result.asked is not None:
        moments.append(result.asked)
    moments.extend(result.nearest)
    data = result.data
    if isinstance(data, ProposalCreated):
        moments.append(data.start_at)
        if data.replaces is not None:
            moments.append(data.replaces)
        moments.extend(data.queued)
    elif isinstance(data, FreeSlots):
        moments.extend(data.slots)
    elif isinstance(data, AppointmentList):
        moments.extend(a.at for a in data.appointments)
    elif isinstance(data, ShopHoursOut):
        moments.extend((data.open, data.close))
    elif isinstance(data, ShopStatusOut):
        if data.until is not None:
            moments.append(data.until)
    elif isinstance(data, ShopOpenOut):
        moments.append(data.at)
        if data.next_change is not None:
            moments.append(data.next_change)
    elif isinstance(data, ResolvedTime):
        if data.start_at is not None:
            moments.append(data.start_at)
    return moments


def _clocks_of_outcome(outcome: PendingOutcome) -> set[ClockKey]:
    clocks = _clocks_of_spoken(outcome.spoken)
    if outcome.appointment is not None:
        clocks |= _clocks_of_moment(outcome.appointment.at)
    for moment in outcome.nearest:
        clocks |= _clocks_of_moment(moment)
    return clocks


def _customer_clocks(question: str) -> set[ClockKey]:
    """Giờ khách vừa nói — guard cho phép nhắc lại ("3 giờ sáng hay 3 giờ
    chiều ạ?"). Khách chưa nói buổi thì mọi buổi của giờ đó đều tính."""
    clocks = _clocks_of_spoken(question)
    for hour, period, minute in tokens.scan_times(question):
        minute = minute or 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if hour == 0 or hour > 12:
            clocks |= _clocks_of_spoken(_clock_phrase(hour, minute))
        elif period is None:
            clocks.update((hour, minute, p) for p in (None, *_PERIODS))
        else:
            clocks.add((hour, minute, period))
    return clocks


def _new_proposals(calls: list[CallRecord]) -> list[ProposalCreated]:
    return [
        c.result.data for c in calls
        if c.allowed and isinstance(c.result, TaskResult) and isinstance(c.result.data, ProposalCreated)
    ]


def _pending_still_held(ctx: TurnContext, pending_outcome: PendingOutcome | None) -> bool:
    """`ctx.pending` vẫn đang chờ sau quyết định của lượt (chưa bị duyệt/từ
    chối/thay ở lượt này)."""
    if ctx.pending is None:
        return False
    if pending_outcome is None or pending_outcome.proposal_id != ctx.pending.id:
        return True
    return pending_outcome.outcome in _STILL_PENDING


def _fallback(
    ctx: TurnContext,
    pending_outcome: PendingOutcome | None,
    new_proposals: list[ProposalCreated],
    pending_held: bool,
) -> str:
    address = ctx.customer.address_phrase
    if pending_outcome is not None and pending_outcome.outcome in _APPROVE_FAILED:
        if _phone_digits():
            return APPROVE_FAILED_FALLBACK.format(
                address=address, when=pending_outcome.spoken, phone=settings.shop_phone,
            )
        # `.env` chưa có SĐT tiệm (bắt buộc trước khi chạy thật): không bịa số.
        return FAILED_FALLBACK.format(address=address, when=pending_outcome.spoken)
    if pending_outcome is not None and pending_outcome.outcome == "approved":
        fact = {
            "kind": _CLAIM_FOR_KIND.get(pending_outcome.kind, pending_outcome.kind),
            "address": address,
            "when": pending_outcome.spoken,
            "context": _tone_context(ctx, pending_outcome, bool(new_proposals)),
        }
        previous = [ctx.last_bot_text] if ctx.last_bot_text else []
        return code_phrase(fact, previous)
    if new_proposals:
        return PROPOSAL_FALLBACK.format(address=address, when=new_proposals[0].start_at.spoken)
    if pending_held and ctx.pending is not None:
        return PROPOSAL_FALLBACK.format(address=address, when=ctx.pending.spoken)
    return GENERIC_FALLBACK.format(address=address)


def build_reply_facts(
    ctx: TurnContext,
    calls: list[CallRecord],
    pending_outcome: PendingOutcome | None,
    question: str,
) -> ReplyFacts:
    """Sự thật code biết của lượt — chỉ đọc call ĐƯỢC PHÉP (`allowed`)."""
    allowed = [c for c in calls if c.allowed]
    new_proposals = _new_proposals(allowed)
    pending_held = _pending_still_held(ctx, pending_outcome)

    # -- mọi mốc giờ có thật ------------------------------------------------
    clocks: set[ClockKey] = set()
    if ctx.pending is not None:
        clocks |= _clock_of(ctx.pending.start_at) | _clocks_of_spoken(ctx.pending.spoken)
        if ctx.pending.target is not None:
            clocks |= _clock_of(ctx.pending.target.start_at)
    if ctx.recently_declined is not None:
        clocks |= _clock_of(ctx.recently_declined.start_at)
    for appointment in ctx.upcoming:
        clocks |= _clock_of(appointment.start_at) | _clocks_of_spoken(appointment.spoken)
    for queued in ctx.queued_requests:
        clocks |= _clock_of(queued.start_at) | _clocks_of_spoken(queued.spoken)
    for hhmm in (ctx.shop_hours.open_time, ctx.shop_hours.close_time):
        clocks |= _clocks_of_iso(hhmm)
    if ctx.unannounced is not None:
        clocks |= _clocks_of_iso(ctx.unannounced.iso) | _clocks_of_spoken(ctx.unannounced.spoken)
    for record in allowed:
        if isinstance(record.result, TaskResult):
            for moment in _moments_of_task(record.result):
                clocks |= _clocks_of_moment(moment)
        elif isinstance(record.result, PendingOutcome):
            clocks |= _clocks_of_outcome(record.result)
    if pending_outcome is not None:
        clocks |= _clocks_of_outcome(pending_outcome)
    clocks |= _customer_clocks(question)

    # -- giờ đang giữ thật / giờ đã ghi thật ----------------------------------
    held: set[ClockKey] = set()
    if pending_held and ctx.pending is not None:
        held |= _clock_of(ctx.pending.start_at)
    for proposal in new_proposals:
        held |= _clocks_of_moment(proposal.start_at)

    done: set[ClockKey] = set()
    allowed_claims: set[Claim] = set()
    if pending_outcome is not None and pending_outcome.outcome == "approved":
        claim = _CLAIM_FOR_KIND.get(pending_outcome.kind)
        if claim:
            allowed_claims.add(claim)
        done |= _clocks_of_spoken(pending_outcome.spoken)
        if pending_outcome.appointment is not None:
            done |= _clocks_of_moment(pending_outcome.appointment.at)
        pending = ctx.pending
        if pending is not None and pending.id == pending_outcome.proposal_id and pending.target is not None:
            done |= _clock_of(pending.target.start_at)  # giờ cũ của lịch vừa dời
    unannounced = ctx.unannounced
    if unannounced is not None and unannounced.outcome == "approved":
        claim = _CLAIM_FOR_KIND.get(unannounced.kind)
        if claim:
            allowed_claims.add(claim)
        done |= _clocks_of_iso(unannounced.iso) | _clocks_of_spoken(unannounced.spoken)

    # -- cụm bắt buộc nhắc --------------------------------------------------
    must_mention: list[str] = []
    if unannounced is not None:
        must_mention.append(unannounced.spoken)
    must_mention.extend(p.start_at.spoken for p in new_proposals)
    needs_phone = pending_outcome is not None and pending_outcome.outcome in _APPROVE_FAILED
    if pending_outcome is not None and (pending_outcome.outcome == "approved" or needs_phone):
        must_mention.append(pending_outcome.spoken)

    return ReplyFacts(
        clocks=clocks,
        held_clocks=held,
        allowed_claims=allowed_claims,
        must_mention=list(dict.fromkeys(s for s in must_mention if s)),
        question_required=bool(new_proposals),
        needs_phone=needs_phone,
        announce_turn_id=unannounced.turn_id if unannounced is not None else None,
        announce_spoken=unannounced.spoken if unannounced is not None else None,
        fallback=_fallback(ctx, pending_outcome, new_proposals, pending_held),
        done_clocks=done,
    )


# ---------------------------------------------------------------------------
# check_reply_text
# ---------------------------------------------------------------------------


def _said_key(key: ClockKey) -> ClockKey:
    """"15 giờ" (máy viết) -> (3, 0, "chiều") để so với các mốc có thật."""
    hour, minute, period = key
    if period is None and 12 < hour <= 23:
        spoken = _clocks_of_spoken(_clock_phrase(hour, minute))
        if spoken:
            return next(iter(spoken))
    return key


def _all_known(said: Iterable[ClockKey], known: set[ClockKey]) -> bool:
    return all(any(draft_checks._same_clock(s, k) for k in known) for s in said)


def _said_clocks(text: str) -> list[ClockKey]:
    return [_said_key(k) for k in draft_checks.clock_keys(text)]


def _clause_violates(clause: str, facts: ReplyFacts) -> bool:
    verbs = draft_checks.claim_verbs(clause)
    if not verbs:
        return False
    said = _said_clocks(clause)
    commit = verbs - {"giữ"}
    if commit:
        # "đã đặt / ghi / chốt / dời / huỷ": chỉ sau `approve` thành công (hoặc
        # R0 approved), và chỉ với đúng giờ đã ghi.
        if any(not (_CLAIMS_FOR_VERB[v] & facts.allowed_claims) for v in commit):
            return True
        return not _all_known(said, facts.done_clocks)
    # Chỉ "giữ": hợp lệ khi giờ đó đang có đề xuất chờ thật (hoặc đã ghi).
    holdable = facts.held_clocks | facts.done_clocks
    if said:
        return not _all_known(said, holdable)
    return not holdable


def _has_claim_violation(text: str, facts: ReplyFacts) -> bool:
    for sentence in tokens.split_sentences(text or ""):
        for clause in tokens.split_clauses(sentence, break_words=("còn",), preserve_date_comma=True):
            if _clause_violates(clause, facts):
                return True
    return False


def _phone_digits() -> str:
    return "".join(tokens.digits_in(settings.shop_phone or ""))


def _mentions_phone(text: str) -> bool:
    phone = _phone_digits()
    return bool(phone) and phone in "".join(tokens.digits_in(text or ""))


def check_reply_text(text: str, facts: ReplyFacts) -> list[str]:
    """Mã vi phạm của câu `text` so với sự thật của lượt, mỗi mã tối đa một
    lần, theo thứ tự `CHECK_CODES`. Rỗng = câu được gửi cho khách."""
    found: set[str] = set()
    if _has_claim_violation(text, facts):
        found.add("claim")
    if not _all_known(_said_clocks(text), facts.clocks):
        found.add("unknown_time")
    normalized = normalize(text)
    if any(normalize(s) not in normalized for s in facts.must_mention):
        found.add("missing_fact")
    if facts.question_required and "?" not in (text or ""):
        found.add("missing_question")
    said_phone = _mentions_phone(text)
    if facts.needs_phone and _phone_digits() and not said_phone:
        found.add("missing_phone")
    if not facts.needs_phone and said_phone:
        found.add("phone_not_allowed")
    return [code for code in CHECK_CODES if code in found]


def is_announced(text: str, facts: ReplyFacts) -> bool:
    """Câu đã báo kết quả R0 (giờ `announce_spoken`) cho khách chưa."""
    if not facts.announce_spoken:
        return False
    return normalize(facts.announce_spoken) in normalize(text)
