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

from datetime import datetime
from typing import Iterable, Literal, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.salon.guard.fallback import compose_fallback_asking
from app.agents.salon.shared.context import TurnSnapshot
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
    TurnFact,
    UnannouncedDecision,
)
from app.agents.salon.shared.text_scan import scan_spoken_clocks, scan_times
from app.core.clock import to_local
from app.core.config import settings
from app.core.errors import UnknownServiceError
from app.core.text import strip_diacritics
from app.core.vi_format import format_vi_hhmm
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService

ClockKey = tuple[int, int, Optional[str]]
Reading = frozenset[tuple[int, int]]
Claim = Literal["booked", "moved", "cancelled"]

CLAIM_FOR_KIND: dict[str, Claim] = {"book": "booked", "move": "moved", "cancel": "cancelled"}

# Giờ đọc 1..12 + buổi -> giờ trong ngày, theo cách người Việt nói (cả cách
# chồng lấn: "6 giờ chiều" = "6 giờ tối" = 18:00, "1 giờ trưa" = "1 giờ chiều").
# Một cặp không có ở đây ("3 giờ tối") không mang giờ nào -> không khớp dữ kiện.
_PERIOD_HOURS: dict[str, dict[int, int]] = {
    "sang": {h: h for h in range(1, 12)},
    "trua": {11: 11, 12: 12, 1: 13, 2: 14},
    "chieu": {12: 12, **{h: h + 12 for h in range(1, 8)}},
    "toi": {12: 0, **{h: h + 12 for h in range(5, 12)}},
    "dem": {12: 0, **{h: h for h in range(1, 5)}, **{h: h + 12 for h in range(9, 12)}},
}

# Quyết định mà câu trả lời phải nêu giờ: kết quả duyệt (xong / thất bại /
# không duyệt được) và hỏi lại khi khách trả lời chưa rõ. `kept` / `rejected` /
# `superseded` không bắt nêu — khách nói chuyện khác hoặc đã bỏ giờ đó.
_MUST_SAY_OUTCOMES = frozenset({"approved", "failed", "not_approvable", "clarify"})
# Nhánh "ghi thất bại" — lượt DUY NHẤT phải (và được) có số điện thoại tiệm.
FAILED_OUTCOMES = frozenset({"failed", "not_approvable"})
# Đề xuất chờ vẫn còn chờ sau quyết định của lượt.
_STILL_PENDING = frozenset({"kept", "clarify"})


class ReplyFacts(BaseModel):
    clocks: set[ClockKey]                 # mọi mốc giờ có thật của lượt
    held_clocks: set[ClockKey]            # giờ đang có đề xuất chờ thật
    # Giờ các lịch ĐANG CÓ của khách (`snapshot.upcoming`) mà KHÔNG xuất hiện ở
    # nguồn dữ kiện nào khác của lượt — "chị đã đặt lịch 3 giờ chiều mai rồi ạ"
    # kể lại một lịch có thật là đúng (Ruling R8). Guard không so ngày, nên một
    # giờ trùng với đề xuất / quyết định / giờ trống của lượt (dù ngày khác)
    # không được lịch đang có bảo chứng (Ruling R9). Chỉ cho động từ đặt / giữ.
    existing_only_clocks: set[ClockKey]
    done_claims: dict[Claim, set[ClockKey]]   # giờ của việc ĐÃ ghi thật, theo loại
    price_ranges: list[tuple[int, int]]   # từng dịch vụ + tổng của nhóm trong cùng lịch
    must_mention: list[str]
    question_required: bool
    needs_phone: bool
    pending_undecided: bool               # có đề xuất chờ, lượt gõ chữ, chưa decide_pending
    announce: Optional[UnannouncedDecision]
    address: str
    previous_replies: list[str]
    fallback: str
    # Agent có phiên mà câu mẫu `fallback` hỏi câu hỏi của nó — phiên đó phải
    # GIỮ mở khi lượt kết thúc bằng câu mẫu (Ruling R12).
    asked_session: Optional[str] = None


# ---------------------------------------------------------------------------
# ClockKey — một dạng giờ cho mọi phía
# ---------------------------------------------------------------------------


def _bare(period: Optional[str]) -> Optional[str]:
    return strip_diacritics(period) if period else None


def clock_of_24h(hour: int, minute: int) -> ClockKey:
    """15:00 -> (15, 0, "chieu"); 00:30 -> (0, 30, "dem"). Nhãn buổi lấy từ
    `format_vi_hhmm` — cùng một nguồn với cách bot đọc giờ cho khách."""
    ((_, _, period),) = scan_spoken_clocks(format_vi_hhmm(f"{hour:02d}:{minute:02d}"))
    return hour, minute, _bare(period)


def readings(hour: int, period: Optional[str], minute: Optional[int]) -> Reading:
    """Mọi giờ trong ngày mà một cách nói `(hour, period, minute)` có thể mang.
    Giờ 24h ("15h", "15:00", "15 giờ chiều") là chính nó; 1..12 chưa kèm buổi
    là hai cách đọc; kèm buổi thì tra `_PERIOD_HOURS`."""
    minute = minute or 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return frozenset()
    if hour == 0 or hour > 12:
        return frozenset({(hour, minute)})
    bare = _bare(period)
    if bare is None:
        return frozenset({(hour % 12, minute), (hour % 12 + 12, minute)})
    hour24 = _PERIOD_HOURS.get(bare, {}).get(hour)
    return frozenset() if hour24 is None else frozenset({(hour24, minute)})


def clocks_in_text(text: str) -> list[Reading]:
    """Mọi giờ trong một câu (câu bot hay câu khách), mỗi giờ là tập cách đọc."""
    return [readings(hour, period, minute) for hour, period, minute in scan_times(text or "")]


def is_known(said: Reading, known: Iterable[ClockKey]) -> bool:
    return any(key[:2] in said for key in known)


def _clocks_of_spoken(spoken: Optional[str]) -> set[ClockKey]:
    clocks: set[ClockKey] = set()
    for hour, minute, period in scan_spoken_clocks(spoken or ""):
        clocks |= {clock_of_24h(h, m) for h, m in readings(hour, period, minute)}
    return clocks


def _clocks_of_moment(moment: Optional[Moment]) -> set[ClockKey]:
    if moment is None:
        return set()
    clocks = _clocks_of_spoken(moment.spoken)
    try:
        local = to_local(datetime.fromisoformat(moment.iso))
        clocks.add(clock_of_24h(local.hour, local.minute))
    except (TypeError, ValueError):
        pass
    return clocks


def _clocks_of_hhmm(hhmm: str) -> set[ClockKey]:
    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
        return {clock_of_24h(hour, minute)}
    except ValueError:
        return set()


def _customer_clocks(question: str) -> set[ClockKey]:
    """Giờ khách vừa nói — bot được nhắc lại. Chưa nói buổi thì cả hai cách
    đọc đều tính (khớp cả khi bot hỏi lại "sáng hay chiều")."""
    return {clock_of_24h(h, m) for said in clocks_in_text(question) for h, m in said}


# ---------------------------------------------------------------------------
# Giá — đọc lại từ danh mục, không tin số trong ServiceFact / chữ model
# ---------------------------------------------------------------------------


async def _services_by_id(catalog: ServiceCatalogService, ids: list[str]) -> dict[str, Service]:
    """Fail-soft như `shared/context.py::_service_names`: id không còn trong
    danh mục thì bỏ qua (không có dải giá nào cho nó), không làm hỏng lượt."""
    if not ids:
        return {}
    try:
        return {service.id: service for service in await catalog.get_many(ids)}
    except UnknownServiceError:
        found = {}
        for service_id in ids:
            service = await catalog.get(service_id)
            if service is not None:
                found[service_id] = service
        return found


async def _price_ranges(db: AsyncIOMotorDatabase, groups: list[list[str]]) -> list[tuple[int, int]]:
    """Dải giá từng dịch vụ được nhắc trong lượt + dải TỔNG của mỗi nhóm nhiều
    dịch vụ trong cùng một lịch (đề xuất chờ, đề xuất mới, phiên chờ)."""
    catalog = ServiceCatalogService(db)
    ids = list(dict.fromkeys(sid for group in groups for sid in group))
    services = await _services_by_id(catalog, ids)
    ranges = [(services[sid].price_min, services[sid].price_max) for sid in ids if sid in services]
    for group in groups:
        unique = list(dict.fromkeys(group))
        if len(unique) > 1 and all(sid in services for sid in unique):
            ranges.append(await catalog.price_range(unique))
    return list(dict.fromkeys(ranges))


# ---------------------------------------------------------------------------
# build_reply_facts
# ---------------------------------------------------------------------------


class _Decision(BaseModel):
    outcome: str
    kind: str
    at: Moment


def _decisions(turn_facts: list[TurnFact], pending_outcome: Optional[PendingOutcome]) -> list[_Decision]:
    """Quyết định của lượt từ `DecisionFact`; `pending_outcome` chỉ là dự phòng
    khi `turn_facts` không có cái nào (lượt rơi về câu mẫu sau lỗi)."""
    found = [_Decision(outcome=f.outcome, kind=f.proposal_kind, at=f.at)
             for f in turn_facts if isinstance(f, DecisionFact)]
    if not found and pending_outcome is not None:
        found = [_Decision(outcome=pending_outcome.outcome, kind=pending_outcome.kind,
                           at=pending_outcome.at)]
    return found


def _opened_session(snapshot: TurnSnapshot, turn_id: str) -> Optional[tuple[str, str]]:
    """(agent, field) của phiên mở / đổi trong lượt này, nếu có."""
    for agent, session in snapshot.sessions.items():
        if session.opened_turn_id == turn_id:
            return agent, session.question.field
    return None


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
    decisions = _decisions(turn_facts, pending_outcome)
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
        clocks |= _clocks_of_moment(moment)
    clocks |= _clocks_of_hhmm(snapshot.shop_hours.open_time)
    clocks |= _clocks_of_hhmm(snapshot.shop_hours.close_time)
    clocks |= _customer_clocks(question)

    # -- giờ đang giữ thật / giờ đã ghi thật ----------------------------------
    pending_held = pending is not None and all(d.outcome in _STILL_PENDING for d in decisions)
    held: set[ClockKey] = set()
    if pending_held:
        held |= _clocks_of_moment(pending.at)
    for proposal in proposals:
        held |= _clocks_of_moment(proposal.at)

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
    other_times = {key[:2] for moment in other for key in _clocks_of_moment(moment)}
    existing_only: set[ClockKey] = set()
    for appt in snapshot.upcoming:
        existing_only |= {key for key in _clocks_of_moment(appt.at) if key[:2] not in other_times}

    done: dict[Claim, set[ClockKey]] = {"booked": set(), "moved": set(), "cancelled": set()}
    approved = [d for d in decisions if d.outcome == "approved" and d.kind in CLAIM_FOR_KIND]
    for decision in approved:
        done[CLAIM_FOR_KIND[decision.kind]] |= _clocks_of_moment(decision.at)
    if unannounced is not None and unannounced.outcome == "approved":
        claim = CLAIM_FOR_KIND.get(unannounced.proposal_kind)
        if claim:
            done[claim] |= _clocks_of_moment(unannounced.at)

    # -- giá ------------------------------------------------------------------
    groups = [[f.service_id] for f in turn_facts if isinstance(f, ServiceFact)]
    if pending is not None:
        groups.append(list(pending.service_ids))
    groups += [list(p.service_ids) for p in proposals]
    for session in snapshot.sessions.values():
        ids = session.memory.get("service_ids")
        if isinstance(ids, list):
            groups.append([sid for sid in ids if isinstance(sid, str)])
    price_ranges = await _price_ranges(db, groups)

    # -- điều bắt buộc --------------------------------------------------------
    must_mention: list[str] = []
    if unannounced is not None:
        must_mention.append(unannounced.at.spoken)
    must_mention += [p.at.spoken for p in proposals]
    # Phát hiện kín thì phải báo khách (chủ dự án 11/11, mục F2): mọi giờ bị
    # báo kín trong lượt phải có trong câu trả lời, kể cả lượt có đề xuất.
    taken = [f.moment for f in turn_facts if isinstance(f, TimeFact) and f.role == "taken"]
    must_mention += [moment.spoken for moment in taken]
    if turn_kind == "typed":
        # Lượt nút bấm: code đã báo quyết định (`button_reply`) — không bắt
        # Orchestrator nhắc lại.
        must_mention += [d.at.spoken for d in decisions if d.outcome in _MUST_SAY_OUTCOMES]
    failed = next((d for d in decisions if d.outcome in FAILED_OUTCOMES), None)
    if turn_kind == "button":
        # Lượt nút bấm: `button_reply` đã báo (kèm số điện thoại nếu thất bại).
        failed = None
    r0_failed = unannounced is not None and unannounced.outcome == "failed"

    # -- câu mẫu code -----------------------------------------------------------
    if proposals:
        held_proposal = (proposals[-1].proposal_kind, proposals[-1].at)
    elif pending_held:
        held_proposal = (pending.kind, pending.at)
    else:
        held_proposal = None
    opened = _opened_session(snapshot, turn_id)
    fallback, asks_session = compose_fallback_asking(
        address=address, shop_phone=settings.shop_phone,
        failed_at=failed.at if failed is not None else None,
        approved=(approved[0].kind, approved[0].at) if approved and turn_kind == "typed" else None,
        proposal=held_proposal, announce=unannounced,
        opened_field=opened[1] if opened is not None else None,
        taken_at=taken[0] if taken else None,
    )

    return ReplyFacts(
        clocks=clocks,
        held_clocks=held,
        existing_only_clocks=existing_only,
        done_claims=done,
        price_ranges=price_ranges,
        must_mention=list(dict.fromkeys(s for s in must_mention if s)),
        question_required=bool(proposals),
        # R0 thất bại cũng là ghi thất bại chưa báo — câu báo nó phải có số.
        needs_phone=failed is not None or r0_failed,
        pending_undecided=turn_kind == "typed" and pending is not None and not pending_decided,
        announce=unannounced,
        address=address,
        previous_replies=list(previous_replies),
        fallback=fallback,
        asked_session=opened[0] if opened is not None and asks_session else None,
    )
