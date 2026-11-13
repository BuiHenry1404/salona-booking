"""Dữ kiện code biết của một lượt (`ReplyFacts`) — guard đối chiếu câu trả lời
cuối của Orchestrator với chúng (spec §7, docs/architech-agent.md luật 5).
Không tin chữ model: mọi giờ/giá ở đây đến từ `TurnSnapshot` (DB), `turn_facts`
(code trích từ ToolMessage), câu khách, và danh mục dịch vụ đọc lại bằng code.

Giờ so theo GIÁ TRỊ TRONG NGÀY, không theo nhãn buổi (review Task 12 #6):
dữ kiện là `ClockKey = (giờ 0..23, phút, nhãn buổi không dấu)` — vd 15:00 ->
`(15, 0, "chieu")`, nhãn chỉ để đọc log; giờ trong câu (bot hay khách) là tập
các giá trị 24h `(giờ, phút)` mà cách nói đó có thể mang (`readings`):
"6 giờ chiều" và "6 giờ tối" cùng là 18:00, "11 giờ trưa" là 11:00, "3 giờ"
chưa kèm buổi là {03:00, 15:00}, "3 giờ sáng" chỉ là 03:00. Bài học Task 8:
`scan_times` trả buổi CÓ DẤU ("tối") — mọi buổi qua `strip_diacritics`
(`_bare`) trước khi tra bảng `_PERIOD_HOURS`.

Không regex (gói `salon` không `import re`).
"""
from __future__ import annotations

from typing import Literal, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.salon.guard.catalog import (
    CLAIM_FOR_KIND,
    Decision,
    FactCatalog,
    build_catalog,
    turn_decisions,
)
from app.agents.salon.guard.clocks import (
    ClockKey,
    Reading,
    clock_of_24h,
    clocks_in_text,
    clocks_of_hhmm,
    clocks_of_moment,
    customer_clocks,
    is_known,
    readings,
)
from app.agents.salon.guard.fallback import GENERIC_FALLBACK
from app.agents.salon.guard.plan import ReplyPlan, build_plan
from app.agents.salon.shared.context import TurnSnapshot
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    TimeFact,
    TurnFact,
    UnannouncedDecision,
)
from app.core.config import settings
from app.services.service_catalog import ServiceCatalogService

Claim = Literal["booked", "moved", "cancelled"]

__all__ = [
    "ClockKey", "Reading", "clock_of_24h", "readings", "clocks_in_text", "is_known",
    "ReplyFacts", "RequiredRef", "build_reply_facts",
]

# Nhánh "ghi thất bại" — lượt DUY NHẤT phải (và được) có số điện thoại tiệm.
FAILED_OUTCOMES = frozenset({"failed", "not_approvable"})
# Đề xuất chờ vẫn còn chờ sau quyết định của lượt.
_STILL_PENDING = frozenset({"kept", "clarify"})
# Đề xuất bị bỏ trong lượt.
_RELEASED_OUTCOMES = frozenset({"rejected", "superseded"})


class RequiredRef(BaseModel):
    """Một dữ kiện câu trả lời cấu trúc (level C) PHẢI trích dẫn — `ref` là id
    trong `FactCatalog` của lượt, `roles` là các vai trò chấp nhận được,
    `todo` là điều câu trả lời phải làm với nó (tiếng Anh, cho lời nhắc viết
    lại — `_TODO_FOR_ROLE`, theo vai trò đầu tiên của `roles`)."""
    ref: str
    roles: list[str]
    todo: str


# Điều câu trả lời phải làm với một dữ kiện bắt buộc, theo vai trò của nó —
# `taken` khác hẳn các vai còn lại (live run 5): không chỉ TRÍCH, mà câu phải
# NÓI nó đã kín trong câu riêng và không bao giờ mời khách chọn (`taken_not_told`).
_TODO_FOR_ROLE: dict[str, str] = {
    "taken": "already booked by another customer: say so in its own sentence and never ask "
             "the customer to choose it",
    "held": "the time being held: state it and ask whether the customer agrees",
    "booked": "booked in this turn: tell the customer",
    "moved": "moved in this turn: tell the customer",
    "cancelled": "cancelled in this turn: tell the customer",
    "failed": "could not be written: tell the customer",
}


class ReplyFacts(BaseModel):
    catalog: FactCatalog                  # dữ kiện đánh id + vai trò của lượt (level C)
    required: list[RequiredRef]           # dữ kiện câu trả lời cấu trúc phải trích dẫn
    clocks: set[ClockKey]                 # mọi mốc giờ có thật của lượt
    held_clocks: set[ClockKey]            # giờ đang có đề xuất chờ thật (+ lịch của đề xuất huỷ bị bỏ)
    # Giờ các lịch ĐANG CÓ của khách (`snapshot.upcoming`) mà KHÔNG xuất hiện ở
    # nguồn dữ kiện nào khác của lượt — "chị đã đặt lịch 3 giờ chiều mai rồi ạ"
    # kể lại một lịch có thật là đúng (Ruling R8). Guard không so ngày, nên một
    # giờ trùng với đề xuất / quyết định / giờ trống của lượt (dù ngày khác)
    # không được lịch đang có bảo chứng (Ruling R9). Chỉ cho động từ đặt / giữ.
    existing_only_clocks: set[ClockKey]
    done_claims: dict[Claim, set[ClockKey]]   # giờ của việc ĐÃ ghi thật, theo loại
    question_required: bool
    needs_phone: bool
    pending_undecided: bool               # có đề xuất chờ, lượt gõ chữ, chưa decide_pending
    announce: Optional[UnannouncedDecision]
    address: str
    previous_replies: list[str]
    fallback: str
    # Kế hoạch câu của lượt (`guard/plan.py`) — `check_reply` đối chiếu câu của
    # model với nó; `fallback` là chữ của nó.
    plan: ReplyPlan
    # Tên mọi dịch vụ của tiệm (danh mục) — `wrong_service` bắt câu nêu một dịch
    # vụ của tiệm không thuộc dữ kiện của lượt.
    shop_service_names: list[str]
    # Agent có phiên mà câu mẫu `fallback` hỏi câu hỏi của nó — phiên đó phải
    # GIỮ mở khi lượt kết thúc bằng câu mẫu (Ruling R12).
    asked_session: Optional[str] = None


# ---------------------------------------------------------------------------
# required — dữ kiện câu trả lời cấu trúc (level C) phải trích dẫn
# ---------------------------------------------------------------------------


def _required_refs(
    *, catalog: FactCatalog, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
    decisions: list[Decision], turn_kind: str, proposals: list[ProposalFact],
) -> list[RequiredRef]:
    """Dữ kiện phải được câu trả lời cấu trúc trích dẫn (spec level C), từ
    các nguồn: R0, đề xuất mới, giờ kín, quyết định của lượt gõ chữ. Bỏ qua mục
    không có id trong `catalog`; mỗi `ref` chỉ giữ MỤC ĐẦU TIÊN."""
    items: list[tuple[Moment, list[str]]] = []
    unannounced = snapshot.unannounced
    if unannounced is not None:
        if unannounced.outcome == "approved":
            claim = CLAIM_FOR_KIND.get(unannounced.proposal_kind)
            if claim is not None:
                items.append((unannounced.at, [claim]))
        elif unannounced.outcome == "failed":
            items.append((unannounced.at, ["failed"]))
    for proposal in proposals:
        items.append((proposal.at, ["held"]))
    for fact in turn_facts:
        if isinstance(fact, TimeFact) and fact.role == "taken":
            items.append((fact.moment, ["taken"]))
    if turn_kind == "typed":
        for decision in decisions:
            if decision.outcome == "approved":
                claim = CLAIM_FOR_KIND.get(decision.kind)
                if claim is not None:
                    items.append((decision.at, [claim]))
            elif decision.outcome in FAILED_OUTCOMES:
                items.append((decision.at, ["failed"]))
            elif decision.outcome == "clarify":
                items.append((decision.at, ["held"]))

    required: list[RequiredRef] = []
    seen: set[str] = set()
    for moment, roles in items:
        ref = catalog.id_of(moment)
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        required.append(RequiredRef(ref=ref, roles=roles, todo=_TODO_FOR_ROLE[roles[0]]))
    return required


# ---------------------------------------------------------------------------
# build_reply_facts
# ---------------------------------------------------------------------------


async def build_reply_facts(
    db: AsyncIOMotorDatabase, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact], turn_id: str,
    turn_kind: Literal["typed", "button"], pending_outcome: Optional[PendingOutcome], question: str,
    previous_replies: list[str], pending_decided: bool,
) -> ReplyFacts:
    """Dựng `ReplyFacts` cho lượt.

    `pending_decided` là cờ `pending_decided` của state (Task 11): một
    `decide_pending` bị từ chối / bị veto vẫn để lại ToolMessage nhưng cờ vẫn
    `False`, nên `pending_undecided` phải đọc cờ này, không đọc lịch sử tool.
    """
    decisions = turn_decisions(turn_facts, pending_outcome)
    proposals = [f for f in turn_facts if isinstance(f, ProposalFact)]
    pending = snapshot.pending
    unannounced = snapshot.unannounced
    address = snapshot.customer.address_phrase

    # -- mọi mốc giờ có thật --------------------------------------------------
    clocks: set[ClockKey] = set()
    moments: list[Optional[Moment]] = [pending.at if pending else None]
    moments += [appt.at for appt in snapshot.upcoming]
    moments.append(unannounced.at if unannounced else None)
    for fact in turn_facts:
        if isinstance(fact, TimeFact):
            moments.append(fact.moment)
        elif isinstance(fact, (ProposalFact, DecisionFact)):
            moments.append(fact.at)
    if pending_outcome is not None:
        # `nearest` (giờ trống gần nhất khi duyệt thất bại) chỉ có ở đây — tool
        # `decide_pending` đưa chúng cho model nên bot được nói.
        moments += [pending_outcome.at, *pending_outcome.nearest]
    for session in snapshot.sessions.values():
        moments += [Moment(iso=option.iso, spoken=option.spoken) for option in session.question.options]
    for moment in moments:
        clocks |= clocks_of_moment(moment)
    clocks |= clocks_of_hhmm(snapshot.shop_hours.open_time)
    clocks |= clocks_of_hhmm(snapshot.shop_hours.close_time)
    clocks |= customer_clocks(question)

    # -- giờ đang giữ thật / giờ đã ghi thật ----------------------------------
    pending_held = pending is not None and all(d.outcome in _STILL_PENDING for d in decisions)
    held: set[ClockKey] = set()
    if pending_held:
        held |= clocks_of_moment(pending.at)
    for proposal in proposals:
        held |= clocks_of_moment(proposal.at)
    # Đề xuất HUỶ bị bỏ (khách không đồng ý / đề xuất khác thay): lịch đó vẫn
    # còn — câu "em giữ nguyên lịch …" của kế hoạch (`REJECTED_CANCEL_REPLY`) đúng.
    for decision in decisions:
        if decision.kind == "cancel" and decision.outcome in _RELEASED_OUTCOMES:
            held |= clocks_of_moment(decision.at)

    # Mọi nguồn giờ KHÁC lịch đang có: đề xuất chờ (bất kể quyết định), đề
    # xuất / quyết định / giờ của lượt (trừ TimeFact "appointment" — chính là
    # lịch đang có), `pending_outcome`, R0, lựa chọn của phiên chờ.
    other: list[Optional[Moment]] = [pending.at if pending else None,
                                     unannounced.at if unannounced else None]
    for fact in turn_facts:
        if isinstance(fact, TimeFact) and fact.role != "appointment":
            other.append(fact.moment)
        elif isinstance(fact, (ProposalFact, DecisionFact)):
            other.append(fact.at)
    if pending_outcome is not None:
        other += [pending_outcome.at, *pending_outcome.nearest]
    for session in snapshot.sessions.values():
        other += [Moment(iso=option.iso, spoken=option.spoken) for option in session.question.options]
    other_times = {key[:2] for moment in other for key in clocks_of_moment(moment)}
    existing_only: set[ClockKey] = set()
    for appt in snapshot.upcoming:
        existing_only |= {key for key in clocks_of_moment(appt.at) if key[:2] not in other_times}

    done: dict[Claim, set[ClockKey]] = {"booked": set(), "moved": set(), "cancelled": set()}
    approved = [d for d in decisions if d.outcome == "approved" and d.kind in CLAIM_FOR_KIND]
    for decision in approved:
        done[CLAIM_FOR_KIND[decision.kind]] |= clocks_of_moment(decision.at)
    if unannounced is not None and unannounced.outcome == "approved":
        claim = CLAIM_FOR_KIND.get(unannounced.proposal_kind)
        if claim:
            done[claim] |= clocks_of_moment(unannounced.at)

    failed = next((d for d in decisions if d.outcome in FAILED_OUTCOMES), None)
    if turn_kind == "button":
        # Lượt nút bấm: `button_reply` đã báo (kèm số điện thoại nếu thất bại).
        failed = None
    r0_failed = unannounced is not None and unannounced.outcome == "failed"

    catalog = await build_catalog(
        db, snapshot=snapshot, turn_facts=turn_facts, pending_outcome=pending_outcome,
        question=question, turn_id=turn_id,
    )
    # -- câu mẫu code: câu của kế hoạch (`guard/plan.py`) ------------------------
    plan = build_plan(
        catalog, snapshot=snapshot, turn_facts=turn_facts, pending_outcome=pending_outcome,
        turn_id=turn_id, turn_kind=turn_kind, shop_phone=settings.shop_phone,
    )
    fallback = plan.text() or GENERIC_FALLBACK.format(address=address)
    shop_service_names = [service.name for service in await ServiceCatalogService(db).list()]

    required = _required_refs(
        catalog=catalog, snapshot=snapshot, turn_facts=turn_facts, decisions=decisions,
        turn_kind=turn_kind, proposals=proposals,
    )

    return ReplyFacts(
        catalog=catalog,
        required=required,
        clocks=clocks,
        held_clocks=held,
        existing_only_clocks=existing_only,
        done_claims=done,
        question_required=bool(proposals),
        # R0 thất bại cũng là ghi thất bại chưa báo — câu báo nó phải có số.
        needs_phone=failed is not None or r0_failed,
        pending_undecided=turn_kind == "typed" and pending is not None and not pending_decided,
        announce=unannounced,
        address=address,
        previous_replies=list(previous_replies),
        fallback=fallback,
        plan=plan,
        shop_service_names=shop_service_names,
        asked_session=plan.asks_session,
    )
