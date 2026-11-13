"""Kế hoạch câu trả lời của lượt (spec plan-guided reply §1, "Quyết định sau
builder check" mục 2-4): CODE quyết định câu trả lời phải nói gì — một danh sách
hành động (`PlanAct`), mỗi hành động có câu mẫu tiếng Việt (mẫu ở
`guard/fallback.py`, giờ đọc bằng nhãn ngắn `short_label`) cùng các giờ / khoảng
giá / dịch vụ câu đó nêu. Model viết lại kế hoạch bằng lời của mình; guard đối
chiếu câu của model với kế hoạch; câu của kế hoạch (`ReplyPlan.text`) cũng là
câu dự phòng của lượt.

Dựng từ `FactCatalog` (của `build_catalog`) cùng các nguồn dữ kiện của lượt
(`TurnSnapshot`, `turn_facts`, `pending_outcome`) — không đọc DB, không tin chữ
model. Lượt không có hành động nào (chào hỏi, cảm ơn) thì kế hoạch rỗng.

Thứ tự hành động: ghi thất bại -> đã đặt / dời / huỷ -> đã bỏ -> giờ kín ->
đề xuất -> mời giờ trống -> hỏi câu của phiên vừa mở -> báo giá -> kể lịch hẹn
-> giờ mở cửa / tiệm bận. Có đề xuất thì không mời giờ trống; mời các lựa chọn
của phiên vừa mở thì không hỏi lại câu của phiên đó; có hành động đặt lịch thì
không báo giá.

Không regex (gói `salon` không `import re`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Literal, Optional, Sequence

from pydantic import BaseModel

from app.agents.salon.guard.catalog import (
    CLAIM_FOR_KIND,
    FactCatalog,
    short_label,
    turn_decisions,
)
from app.agents.salon.guard.clocks import clocks_of_spoken
from app.agents.salon.guard.fallback import (
    ALSO_FAILED_REPLY,
    APPOINTMENTS_REPLY,
    BUSY_REPLY,
    FIELD_QUESTION,
    HOURS_REPLY,
    NEAREST_OFFER,
    TAKEN_REPLY,
    TIME_OPTIONS_QUESTION,
    _capitalized,
    _done,
    _failed,
    _price,
    _proposal,
    _released,
    _spoken_list,
)
from app.agents.salon.shared.context import TurnSnapshot
from app.agents.salon.shared.schemas import (
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
    TurnFact,
)
from app.core.clock import to_local
from app.core.vi_format import format_vi_hhmm
from app.models.waiting_session import WaitingSession

ActKind = Literal["failed", "done", "taken", "proposal", "offer", "ask_field", "released",
                  "price", "appointments", "hours", "busy"]

PLAN_HEADER = "Reply plan (tell the customer all of this, in your own words):"

# Quyết định "ghi thất bại" — câu DUY NHẤT có số điện thoại tiệm.
_FAILED_OUTCOMES = frozenset({"failed", "not_approvable"})
_RELEASED_OUTCOMES = frozenset({"rejected", "superseded"})
# Hành động của việc đặt lịch — có một trong số này thì kế hoạch không báo giá.
_BOOKING_ACTS = frozenset({"failed", "done", "released", "taken", "proposal", "offer", "ask_field"})
# Mời tối đa ngần này giờ `free` của lượt (ruling: trần của kế hoạch; các giờ
# trống khác model vẫn được nói — `unknown_time` cho phép).
_FREE_OFFER_CAP = 3

Clock = tuple[int, int]
PriceRange = tuple[int, int]


class PlanAct(BaseModel):
    kind: ActKind
    sentence: str                          # câu mẫu tiếng Việt của hành động
    clocks: list[Clock] = []               # giờ 24h câu trả lời phải nói (tất cả)
    prices: list[PriceRange] = []          # mỗi khoảng cần một số tiền nằm trong nó
    service_names: list[str] = []          # dịch vụ hành động nói tới (đề xuất / quyết định / giá)
    taken_clocks: list[Clock] = []         # chỉ hành động "taken"
    announce_turn_ids: list[str] = []      # lượt trong sổ mà hành động này báo


class ReplyPlan(BaseModel):
    acts: list[PlanAct]
    # Agent có phiên vừa mở mà kế hoạch hỏi câu hỏi của nó — phiên đó phải GIỮ
    # mở khi lượt kết thúc bằng câu mẫu (Ruling R12).
    asks_session: Optional[str] = None

    def text(self) -> str:
        return " ".join(_capitalized(act.sentence) for act in self.acts)

    def render(self) -> str:
        if not self.acts:
            return ""
        return "\n".join([PLAN_HEADER, *(f"- {act.sentence}" for act in self.acts)])

    def clocks(self) -> set[Clock]:
        return {clock for act in self.acts for clock in act.clocks}

    def taken_clocks(self) -> set[Clock]:
        return {clock for act in self.acts for clock in act.taken_clocks}

    def offered_clocks(self) -> set[Clock]:
        return {clock for act in self.acts if act.kind in ("offer", "proposal")
                for clock in act.clocks}

    def prices(self) -> list[PriceRange]:
        return list(dict.fromkeys(price for act in self.acts for price in act.prices))

    def announce_turn_ids(self) -> list[str]:
        return list(dict.fromkeys(tid for act in self.acts for tid in act.announce_turn_ids))


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _key(moment: Moment) -> str:
    """Khoá gộp mốc giờ trùng: UTC ISO khi đọc được múi giờ, không thì iso thô."""
    try:
        dt = datetime.fromisoformat(moment.iso)
    except ValueError:
        return moment.iso
    return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo is not None else moment.iso


def _distinct(moments: Iterable[Moment]) -> list[Moment]:
    seen: set[str] = set()
    found: list[Moment] = []
    for moment in moments:
        key = _key(moment)
        if key not in seen:
            seen.add(key)
            found.append(moment)
    return found


def _local(moment: Moment) -> Optional[datetime]:
    try:
        return to_local(datetime.fromisoformat(moment.iso))
    except (TypeError, ValueError):
        return None


def _clocks(moments: Iterable[Moment]) -> list[Clock]:
    """Giờ 24h của các mốc, theo thứ tự, không lặp. Giờ đọc từ `iso` (giá trị
    thật); iso không đọc được thì từ `spoken` (chính là nhãn câu mẫu nêu)."""
    found: list[Clock] = []
    for moment in moments:
        local = _local(moment)
        if local is not None:
            values = [(local.hour, local.minute)]
        else:
            values = sorted({key[:2] for key in clocks_of_spoken(moment.spoken)})
        found += [value for value in values if value not in found]
    return found


def _hhmm_phrase(moment: Moment) -> str:
    """"8 giờ sáng" — giờ mở / đóng cửa không kèm ngày."""
    local = _local(moment)
    if local is None:
        return moment.spoken
    return format_vi_hhmm(f"{local.hour:02d}:{local.minute:02d}")


class _Services:
    """Tên + khoảng giá dịch vụ theo id, đọc từ các mục `service` của danh mục."""

    def __init__(self, catalog: FactCatalog) -> None:
        self._by_id: dict[str, tuple[str, tuple[int, int]]] = {}
        for entry in catalog.entries:
            if entry.kind == "service" and entry.service_ids and entry.names and entry.price:
                self._by_id.setdefault(entry.service_ids[0], (entry.names[0], entry.price))

    def names(self, service_ids: Sequence[str]) -> list[str]:
        return [self._by_id[sid][0] for sid in dict.fromkeys(service_ids) if sid in self._by_id]

    def price_act(self, service_id: str) -> Optional[PlanAct]:
        if service_id not in self._by_id:
            return None
        name, (low, high) = self._by_id[service_id]
        return PlanAct(kind="price", sentence=_price(name, low, high), prices=[(low, high)],
                       service_names=[name])


def _opened_session(snapshot: TurnSnapshot, turn_id: str) -> Optional[tuple[str, WaitingSession]]:
    """(agent, phiên) mở / đổi trong lượt này, nếu có."""
    for agent, session in snapshot.sessions.items():
        if session.opened_turn_id == turn_id:
            return agent, session
    return None


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


def build_plan(
    catalog: FactCatalog, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
    pending_outcome: Optional[PendingOutcome], turn_id: str,
    turn_kind: Literal["typed", "button"], shop_phone: Optional[str],
) -> ReplyPlan:
    today = snapshot.today
    address = snapshot.customer.address_phrase
    services = _Services(catalog)
    pending = snapshot.pending
    unannounced = snapshot.unannounced
    # Lượt nút bấm: `button_reply` đã báo quyết định của chính lượt đó.
    decisions = turn_decisions(turn_facts, pending_outcome) if turn_kind == "typed" else []
    acts: list[PlanAct] = []
    asks_session: Optional[str] = None

    def label(moment: Moment) -> str:
        return short_label(moment, today)

    def decision_services(kind: str, at: Moment) -> list[str]:
        """Dịch vụ của đề xuất mà quyết định này nói tới, từ `pending_outcome`
        (cùng loại, cùng giờ) — sau khi duyệt, `snapshot.pending` đã là None."""
        if (pending_outcome is not None and pending_outcome.kind == kind
                and _key(pending_outcome.at) == _key(at)):
            return services.names(pending_outcome.service_ids)
        return []

    # R0 của CHÍNH lượt này: `decide_pending` ghi dòng ledger của lượt (chưa
    # báo) và làm snapshot cũ, nên snapshot đọc lại thấy quyết định của lượt
    # là "chưa báo" — câu của lượt đã báo nó, không báo lần hai.
    r0 = unannounced if unannounced is not None and unannounced.turn_id != turn_id else None

    # -- 1. ghi thất bại --------------------------------------------------------
    failed = next((d for d in decisions if d.outcome in _FAILED_OUTCOMES), None)
    if failed is not None:
        acts.append(PlanAct(
            kind="failed", sentence=_failed(label(failed.at), address=address, shop_phone=shop_phone),
            clocks=_clocks([failed.at]),
            announce_turn_ids=[turn_id] if failed.outcome == "failed" else []))
    if r0 is not None and r0.outcome == "failed":
        r0_at = label(r0.at)
        sentence = (ALSO_FAILED_REPLY.format(at=r0_at) if failed is not None
                    else _failed(r0_at, address=address, shop_phone=shop_phone))
        acts.append(PlanAct(kind="failed", sentence=sentence, clocks=_clocks([r0.at]),
                            announce_turn_ids=[r0.turn_id]))

    # -- 2. đã đặt / dời / huỷ ---------------------------------------------------
    approved = next((d for d in decisions
                     if d.outcome == "approved" and d.kind in CLAIM_FOR_KIND), None)
    if approved is not None:
        names = decision_services(approved.kind, approved.at)
        acts.append(PlanAct(
            kind="done", sentence=_done(approved.kind, label(approved.at), address=address,
                                        service_names=names),
            clocks=_clocks([approved.at]), service_names=names, announce_turn_ids=[turn_id]))
    if r0 is not None and r0.outcome == "approved" and r0.proposal_kind in CLAIM_FOR_KIND:
        # `UnannouncedDecision` không mang dịch vụ — câu R0 không nêu tên dịch vụ.
        acts.append(PlanAct(
            kind="done", sentence=_done(r0.proposal_kind, label(r0.at), address=address),
            clocks=_clocks([r0.at]), announce_turn_ids=[r0.turn_id]))

    # -- 3. đã bỏ ---------------------------------------------------------------
    released = next((d for d in decisions if d.outcome in _RELEASED_OUTCOMES), None)
    if released is not None:
        acts.append(PlanAct(
            kind="released", sentence=_released(released.kind, label(released.at), address=address),
            clocks=_clocks([released.at])))

    # -- 4. giờ kín (luôn báo, chủ dự án 11/11) -----------------------------------
    taken = _distinct(f.moment for f in turn_facts if isinstance(f, TimeFact) and f.role == "taken")
    for moment in taken:
        clocks = _clocks([moment])
        acts.append(PlanAct(kind="taken", sentence=TAKEN_REPLY.format(at=label(moment)),
                            clocks=clocks, taken_clocks=clocks))

    # -- 5. đề xuất -------------------------------------------------------------
    proposals = [f for f in turn_facts if isinstance(f, ProposalFact)]
    proposal: Optional[tuple[str, Moment, list[str]]] = None
    if proposals:
        last = proposals[-1]
        proposal = (last.proposal_kind, last.at, list(last.service_ids))
    elif pending is not None and any(d.outcome == "clarify" for d in decisions):
        # Chỉ `clarify` hỏi lại đề xuất chờ; `kept` (khách nói chuyện khác) thì
        # không bắt nhắc lại (spec quyết định 4).
        proposal = (pending.kind, pending.at, list(pending.service_ids))
    if proposal is not None:
        kind, at, proposal_ids = proposal
        names = services.names(proposal_ids)
        acts.append(PlanAct(
            kind="proposal", sentence=_proposal(kind, label(at), address=address, service_names=names),
            clocks=_clocks([at]), service_names=names))

    # -- 6. mời giờ trống (không có đề xuất) ----------------------------------------
    opened = _opened_session(snapshot, turn_id)
    if proposal is None:
        offer: list[Moment] = []
        from_nearest = False
        if opened is not None and opened[1].question.field == "time" and opened[1].question.options:
            offer = _distinct(Moment(iso=o.iso, spoken=o.spoken) for o in opened[1].question.options)
            asks_session = opened[0]
        elif (pending_outcome is not None and pending_outcome.nearest
                and pending_outcome.kind != "cancel"):
            offer, from_nearest = _distinct(pending_outcome.nearest), True
        else:
            free = _distinct(f.moment for f in turn_facts if isinstance(f, TimeFact) and f.role == "free")
            offer = free[:_FREE_OFFER_CAP]
        if offer:
            listed = _spoken_list([label(moment) for moment in offer])
            sentence = (NEAREST_OFFER.format(nearest=listed, address=address) if from_nearest
                        else TIME_OPTIONS_QUESTION.format(options=listed, address=address))
            acts.append(PlanAct(kind="offer", sentence=sentence, clocks=_clocks(offer)))

    # -- 7. hỏi câu của phiên vừa mở ------------------------------------------------
    if opened is not None and asks_session is None and opened[1].question.field in FIELD_QUESTION:
        acts.append(PlanAct(kind="ask_field",
                            sentence=FIELD_QUESTION[opened[1].question.field].format(address=address)))
        asks_session = opened[0]

    # -- 8. báo giá (chỉ lượt không có hành động đặt lịch) -------------------------
    # Agent Dịch vụ đánh `identified` cho dịch vụ khách nêu khi đặt lịch ("đặt sơn
    # gel 3 giờ"), nên báo giá bắt buộc ở lượt đặt lịch sẽ ép câu nào cũng nêu giá
    # (ruling controller, Task 2). Lượt đặt lịch vẫn được nêu giá — `unknown_price`
    # kiểm số tiền.
    if not any(act.kind in _BOOKING_ACTS for act in acts):
        priced = [f.service_id for f in turn_facts if isinstance(f, ServiceFact) and f.identified]
        for service_id in dict.fromkeys(priced):
            act = services.price_act(service_id)
            if act is not None:
                acts.append(act)

    # -- 9. kể lịch hẹn -------------------------------------------------------------
    appointments = _distinct(f.moment for f in turn_facts
                             if isinstance(f, TimeFact) and f.role == "appointment")
    if appointments:
        times = _spoken_list([label(moment) for moment in appointments], conjunction="và")
        acts.append(PlanAct(kind="appointments",
                            sentence=APPOINTMENTS_REPLY.format(address=address, times=times),
                            clocks=_clocks(appointments)))

    # -- 10. giờ mở cửa / tiệm bận ----------------------------------------------------
    def first_time(role: str) -> Optional[Moment]:
        return next((f.moment for f in turn_facts if isinstance(f, TimeFact) and f.role == role), None)

    shop_open, shop_close = first_time("shop_open"), first_time("shop_close")
    if shop_open is not None and shop_close is not None:
        acts.append(PlanAct(
            kind="hours",
            sentence=HOURS_REPLY.format(open=_hhmm_phrase(shop_open), close=_hhmm_phrase(shop_close)),
            clocks=_clocks([shop_open, shop_close])))
    busy_until = first_time("busy_until")
    if busy_until is not None:
        acts.append(PlanAct(kind="busy", sentence=BUSY_REPLY.format(at=label(busy_until)),
                            clocks=_clocks([busy_until])))

    for act in acts:
        act.sentence = _capitalized(act.sentence)
    return ReplyPlan(acts=acts, asks_session=asks_session)
