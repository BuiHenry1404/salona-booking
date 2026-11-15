"""Kế hoạch câu trả lời của lượt (spec guard rebuild, mục "Planner"): CODE quyết
định câu trả lời phải nói gì — các hành động (`PlanAct`), câu hỏi của phiên vừa
mở (`PlanAsk`) và các giờ kín (code tự báo, model không nhắc). `to_json()` là
phần Orchestrator đọc trong `<reply_plan>…</reply_plan>`; bên trong, mỗi hành
động còn giữ giờ (`Clock`), khoảng giá số và tên dịch vụ để validator so.

Chỉ dữ kiện, không câu tiếng Việt (câu mẫu ở `templates.py`). Mọi giờ trong
JSON là nhãn nói (`short_label`; giờ mở / đóng cửa chỉ cụm giờ), mọi giá là
chuỗi nói (`spoken_price`).

Thứ tự hành động: failed → done → released → proposal → offer → price →
appointments → hours → busy.

Không regex (gói `salon` không `import re`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Literal, Optional, get_args

from pydantic import BaseModel

from app.agents.salon.guard.clocks import clocks_of_spoken
from app.agents.salon.guard.results import (
    FAILED_OUTCOMES,
    RELEASED_OUTCOMES,
    Clock,
    TurnResults,
    instant_of,
    short_label,
    spoken_price,
)
from app.agents.salon.shared.schemas import (
    AgentStatusFact,
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    QuestionField,
    TimeFact,
)
from app.agents.salon.shared.text_scan import digits_in
from app.core.clock import to_local
from app.core.vi_format import format_vi_hhmm
from app.models.waiting_session import WaitingSession

ActKind = Literal["failed", "done", "released", "proposal", "offer", "price", "appointments",
                  "hours", "busy"]

# Loại đề xuất mà `done` báo được (đặt / dời / huỷ).
_DECISION_KINDS = frozenset({"book", "move", "cancel"})
# Hành động của việc đặt lịch — có một trong số này (hoặc giờ kín, hoặc `ask`)
# thì dịch vụ `identified` không bị bắt báo giá: agent Dịch vụ đánh `identified`
# cho dịch vụ khách nêu khi đặt lịch ("đặt sơn gel 3 giờ").
_BOOKING_ACTS = frozenset({"failed", "done", "released", "proposal", "offer"})
# Mời tối đa ngần này giờ; `nearest` của `failed` cũng tối đa ngần này.
_OFFER_CAP = 3
# Kể tối đa ngần này lịch hẹn.
_APPOINTMENTS_CAP = 5
_QUESTION_FIELDS = frozenset(get_args(QuestionField))


class PlanAct(BaseModel):
    act: ActKind
    fields: dict[str, Any]                 # đúng các trường JSON của hành động (spec)
    clocks: list[Clock] = []               # giờ câu trả lời phải nói (tất cả)
    prices: list[tuple[int, int]] = []     # mỗi khoảng cần một số tiền nằm trong nó
    service_names: list[str] = []          # dịch vụ câu trả lời phải nêu
    announce_turn_ids: list[str] = []      # lượt trong sổ mà hành động này báo


class PlanAsk(BaseModel):
    fields: list[str]                      # các `QuestionField`, hỏi gộp một câu
    options: list[str]                     # nhãn nói các lựa chọn của phiên
    agent: str                             # agent có phiên vừa mở


class ReplyPlan(BaseModel):
    taken: list[str] = []                  # nhãn nói các giờ kín (code báo)
    taken_clocks: list[Clock] = []
    acts: list[PlanAct] = []
    ask: Optional[PlanAsk] = None
    # Số tiệm mà câu trả lời phải nêu (`failed.phone`), không thì None.
    phone: Optional[str] = None
    # Agent có phiên vừa mở mà kế hoạch hỏi câu của nó (qua `ask`, hoặc qua
    # `offer` dựng từ lựa chọn của phiên) — phiên đó phải GIỮ mở khi lượt kết
    # thúc bằng câu mẫu (Ruling R12).
    asked_session: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "taken_told_by_code": list(self.taken),
            "acts": [{"act": act.act, **act.fields} for act in self.acts],
            "ask": (None if self.ask is None
                    else {"fields": list(self.ask.fields), "options": list(self.ask.options)}),
        }

    def is_empty(self) -> bool:
        return not self.acts and self.ask is None and not self.taken

    def act_clocks(self) -> set[Clock]:
        return {clock for act in self.acts for clock in act.clocks}

    def announce_turn_ids(self) -> list[str]:
        return list(dict.fromkeys(tid for act in self.acts for tid in act.announce_turn_ids))


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


class _Decision(BaseModel):
    outcome: str
    kind: str
    at: Moment


def _key(moment: Moment) -> str:
    """Khoá gộp mốc trùng: UTC ISO khi đọc được múi giờ, không thì iso thô."""
    return instant_of(moment) or moment.iso


def _distinct(moments: Iterable[Moment]) -> list[Moment]:
    seen: set[str] = set()
    found: list[Moment] = []
    for moment in moments:
        if _key(moment) not in seen:
            seen.add(_key(moment))
            found.append(moment)
    return found


def _local(moment: Moment) -> Optional[datetime]:
    try:
        return to_local(datetime.fromisoformat(moment.iso))
    except (TypeError, ValueError):
        return None


def _clocks(moments: Iterable[Moment]) -> list[Clock]:
    """Giờ (giờ, phút) của các mốc, theo thứ tự, không lặp. Đọc từ `iso` (giá
    trị thật); iso không đọc được thì từ `spoken`."""
    found: list[Clock] = []
    for moment in moments:
        local = _local(moment)
        if local is not None:
            values = [(local.hour, local.minute)]
        else:
            values = sorted({key[:2] for key in clocks_of_spoken(moment.spoken)})
        found += [value for value in values if value not in found]
    return found


def _clock_phrase(moment: Moment) -> str:
    """"8 giờ sáng" — giờ mở / đóng cửa không kèm ngày."""
    local = _local(moment)
    if local is None:
        return moment.spoken
    return format_vi_hhmm(f"{local.hour:02d}:{local.minute:02d}")


def _turn_decisions(results: TurnResults) -> list[_Decision]:
    """Quyết định của lượt gõ chữ, từ `DecisionFact`; `pending_outcome` chỉ là dự
    phòng khi không có cái nào (lượt rơi về câu mẫu sau lỗi). Lượt nút bấm:
    `button_reply` đã báo quyết định của chính lượt đó — không có."""
    if results.turn_kind != "typed":
        return []
    found = [_Decision(outcome=f.outcome, kind=f.proposal_kind, at=f.at)
             for f in results.turn_facts if isinstance(f, DecisionFact)]
    outcome = results.pending_outcome
    if not found and outcome is not None:
        found = [_Decision(outcome=outcome.outcome, kind=outcome.kind, at=outcome.at)]
    return found


def _times(results: TurnResults, role: str) -> list[Moment]:
    return _distinct(f.moment for f in results.turn_facts
                     if isinstance(f, TimeFact) and f.role == role)


def _opened_session(results: TurnResults) -> Optional[tuple[str, WaitingSession]]:
    """(agent, phiên) mở / đổi trong lượt này, nếu có."""
    for agent, session in results.snapshot.sessions.items():
        if session.opened_turn_id == results.turn_id:
            return agent, session
    return None


# Phiên chờ của lượt trước được hỏi lại theo thứ tự này (residual R4).
_REASK_AGENTS = ("appointment", "service")


def _earlier_session(results: TurnResults) -> Optional[tuple[str, WaitingSession]]:
    """Phiên chờ mở ở một lượt TRƯỚC, còn chờ, còn hạn, mà agent của nó không
    chạy trong lượt này — khách nói chuyện khác thì câu hỏi của phiên vẫn phải
    hỏi lại (live 15/11). Có đề xuất chờ thì không (luật `decide_pending` /
    `clarify` quyết)."""
    snapshot = results.snapshot
    if snapshot.pending is not None:
        return None
    ran = {fact.agent for fact in results.turn_facts if isinstance(fact, AgentStatusFact)}
    for agent in _REASK_AGENTS:
        session = snapshot.sessions.get(agent)
        if (session is not None and session.status == "waiting"
                and session.opened_turn_id != results.turn_id and agent not in ran
                and session.expires_at > snapshot.now_local):
            return agent, session
    return None


def _session_options(session: WaitingSession) -> list[Moment]:
    return _distinct(Moment(iso=o.iso, spoken=o.spoken) for o in session.question.options)


class _Planner:
    def __init__(self, results: TurnResults) -> None:
        self.results = results
        self.acts: list[PlanAct] = []

    def label(self, moment: Moment) -> str:
        return short_label(moment, self.results.today)

    def names(self, service_ids: Iterable[str]) -> list[str]:
        services = self.results.services
        return [services[sid].name for sid in dict.fromkeys(service_ids) if sid in services]

    def outcome_of(self, decision: _Decision) -> Optional[PendingOutcome]:
        """`pending_outcome` nói tới quyết định này (cùng loại, cùng giờ) — nguồn
        dịch vụ và `nearest` (sau khi duyệt, `snapshot.pending` đã là None)."""
        outcome = self.results.pending_outcome
        if outcome is None or outcome.kind != decision.kind:
            return None
        return outcome if _key(outcome.at) == _key(decision.at) else None

    def add(self, act: ActKind, fields: dict[str, Any], *, moments: Iterable[Moment] = (),
            prices: Iterable[tuple[int, int]] = (), service_names: Iterable[str] = (),
            announce: Iterable[str] = ()) -> None:
        self.acts.append(PlanAct(act=act, fields=fields, clocks=_clocks(moments),
                                 prices=list(prices), service_names=list(service_names),
                                 announce_turn_ids=list(announce)))

    def failed(self, at: Moment, nearest: list[Moment], phone: Optional[str],
               announce: list[str]) -> None:
        nearest = _distinct(nearest)[:_OFFER_CAP]
        self.add("failed", {"at": self.label(at), "nearest": [self.label(m) for m in nearest],
                            "phone": phone if not nearest else None},
                 moments=[at, *nearest], announce=announce)


def _phone(results: TurnResults) -> Optional[str]:
    phone = results.shop_phone
    return phone if phone and digits_in(phone) else None


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


def build_plan(results: TurnResults) -> ReplyPlan:
    planner = _Planner(results)
    label = planner.label
    snapshot = results.snapshot
    turn_id = results.turn_id
    decisions = _turn_decisions(results)
    # R0 của CHÍNH lượt này: `TurnLedger.unannounced` đã bỏ lượt đang chạy
    # (review F8), nhưng snapshot dựng không có `turn_id` (test, công cụ) vẫn
    # có thể thấy quyết định của lượt là "chưa báo" — câu của lượt đã báo nó,
    # không báo lần hai.
    r0 = snapshot.unannounced
    if r0 is not None and r0.turn_id == turn_id:
        r0 = None

    # -- failed ------------------------------------------------------------------
    failed = next((d for d in decisions if d.outcome in FAILED_OUTCOMES), None)
    if failed is not None:
        outcome = planner.outcome_of(failed)
        # Huỷ thất bại thì không mời giờ khác.
        nearest = list(outcome.nearest) if outcome is not None and failed.kind != "cancel" else []
        planner.failed(failed.at, nearest, _phone(results),
                       [turn_id] if failed.outcome == "failed" else [])
    if r0 is not None and r0.outcome == "failed":
        # Lần thất bại thứ hai trong cùng lượt không kèm số tiệm.
        planner.failed(r0.at, [], None if failed is not None else _phone(results), [r0.turn_id])

    # -- done --------------------------------------------------------------------
    approved = next((d for d in decisions
                     if d.outcome == "approved" and d.kind in _DECISION_KINDS), None)
    if approved is not None:
        outcome = planner.outcome_of(approved)
        names = planner.names(outcome.service_ids) if outcome is not None else []
        planner.add("done", {"kind": approved.kind, "at": label(approved.at), "services": names},
                    moments=[approved.at], service_names=names, announce=[turn_id])
    if r0 is not None and r0.outcome == "approved" and r0.proposal_kind in _DECISION_KINDS:
        # `UnannouncedDecision` không mang dịch vụ.
        planner.add("done", {"kind": r0.proposal_kind, "at": label(r0.at), "services": []},
                    moments=[r0.at], announce=[r0.turn_id])

    # -- released ----------------------------------------------------------------
    released = next((d for d in decisions if d.outcome in RELEASED_OUTCOMES), None)
    if released is not None:
        planner.add("released", {"kind": released.kind, "at": label(released.at)},
                    moments=[released.at])

    # -- proposal ----------------------------------------------------------------
    proposals = [f for f in results.turn_facts if isinstance(f, ProposalFact)]
    proposal: Optional[tuple[str, Moment, list[str]]] = None
    if proposals:
        last = proposals[-1]
        proposal = (last.proposal_kind, last.at, list(last.service_ids))
    elif snapshot.pending is not None and any(d.outcome == "clarify" for d in decisions):
        # Chỉ `clarify` hỏi lại đề xuất chờ; `kept` (khách nói chuyện khác) thì không.
        pending = snapshot.pending
        proposal = (pending.kind, pending.at, list(pending.service_ids))
    if proposal is not None:
        kind, at, service_ids = proposal
        # Câu huỷ không nêu dịch vụ.
        names = [] if kind == "cancel" else planner.names(service_ids)
        planner.add("proposal", {"kind": kind, "at": label(at), "services": names},
                    moments=[at], service_names=names)

    # -- offer / ask -------------------------------------------------------------
    opened = _opened_session(results)
    asked_session: Optional[str] = None
    offered_session = False
    if proposal is None:
        offer: list[Moment] = []
        if opened is not None and "time" in opened[1].question.fields:
            offer = _session_options(opened[1])[:_OFFER_CAP]
            offered_session = bool(offer)
        # `failed.nearest` đã mời các giờ gần nhất — không mời thêm giờ trống
        # (kế hoạch cũ: mời `nearest` HOẶC giờ trống, không cả hai).
        nearest_offered = any(act.act == "failed" and act.fields["nearest"]
                              for act in planner.acts)
        if not offer and not nearest_offered:
            offer = _times(results, "free")[:_OFFER_CAP]
        if offer:
            planner.add("offer", {"times": [label(m) for m in offer]}, moments=offer)
    ask: Optional[PlanAsk] = None
    if opened is not None:
        agent, session = opened
        if offered_session or (proposal is not None and session.question.fields == ["confirm"]):
            # Đề xuất đã hỏi "đồng ý không" — không hỏi xác nhận lần hai; phiên
            # vẫn giữ mở (R12), không thì yêu cầu đang xếp hàng bị mất.
            asked_session = agent
        else:
            fields = [f for f in session.question.fields if f in _QUESTION_FIELDS]
            if fields:
                ask = PlanAsk(fields=fields, agent=agent,
                              options=[label(m) for m in _session_options(session)])
                asked_session = agent

    # -- ask lại phiên chờ của lượt trước (residual R4) -----------------------
    if proposal is None and ask is None and not any(act.act == "offer" for act in planner.acts):
        waiting = _earlier_session(results)
        if waiting is not None:
            agent, session = waiting
            # `confirm` hỏi về một đề xuất; không còn đề xuất chờ thì không có
            # gì để xác nhận (phiên `confirm` chỉ giữ yêu cầu kế, spec §5.1).
            fields = [f for f in session.question.fields
                      if f in _QUESTION_FIELDS and f != "confirm"]
            if fields:
                ask = PlanAsk(fields=fields, agent=agent,
                              options=[label(m) for m in _session_options(session)])
                asked_session = agent

    # -- price -------------------------------------------------------------------
    taken = _times(results, "taken")
    booking = (taken or ask is not None
               or any(act.act in _BOOKING_ACTS for act in planner.acts))
    for service_id in results.turn_service_ids:
        item = results.services[service_id]
        if item.asked_price or (item.identified and not booking):
            planner.add("price", {"service": item.name,
                                  "price": spoken_price(item.price_min, item.price_max)},
                        prices=[(item.price_min, item.price_max)], service_names=[item.name])

    # -- appointments ------------------------------------------------------------
    appointments = _times(results, "appointment")[:_APPOINTMENTS_CAP]
    if appointments:
        planner.add("appointments", {"times": [label(m) for m in appointments]},
                    moments=appointments)

    # -- hours / busy ------------------------------------------------------------
    shop_open, shop_close = _times(results, "shop_open"), _times(results, "shop_close")
    if shop_open and shop_close:
        planner.add("hours", {"open": _clock_phrase(shop_open[0]),
                              "close": _clock_phrase(shop_close[0])},
                    moments=[shop_open[0], shop_close[0]])
    busy_until = _times(results, "busy_until")
    if busy_until:
        planner.add("busy", {"until": label(busy_until[0])}, moments=[busy_until[0]])

    acts = planner.acts
    return ReplyPlan(
        taken=[label(m) for m in taken],
        taken_clocks=_clocks(taken),
        acts=acts,
        ask=ask,
        phone=next((act.fields["phone"] for act in acts
                    if act.act == "failed" and act.fields["phone"]), None),
        asked_session=asked_session,
    )
