"""Dữ kiện lượt đánh số id + vai trò, để câu trả lời cuối của Orchestrator trích
dẫn thay vì tự nhắc lại giờ/giá (spec level C §2-§3). `build_catalog` dựng
`FactCatalog` từ đúng các nguồn `guard/facts.py::build_reply_facts` đã đọc
(`TurnSnapshot`, `turn_facts`, `pending_outcome`, câu khách, danh mục dịch vụ);
`ContextMiddleware` hiện danh mục trong khối bối cảnh, `build_reply_facts` dựng
cùng danh mục cho guard.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional, get_args

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.salon.guard.clocks import clocks_in_text, clocks_of_hhmm, clocks_of_moment
from app.agents.salon.shared.context import TurnSnapshot
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
    TurnFact,
)
from app.core.clock import to_local
from app.core.errors import UnknownServiceError
from app.core.vi_format import format_vi_hhmm
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService

SegmentRole = Literal["offer", "taken", "held", "booked", "moved", "cancelled", "appointment",
                      "asked", "hours", "service", "price", "failed", "released", "busy"]
SEGMENT_ROLES: tuple[str, ...] = get_args(SegmentRole)
ANNOUNCE_ROLES = frozenset({"booked", "moved", "cancelled", "failed"})

Claim = Literal["booked", "moved", "cancelled"]
CLAIM_FOR_KIND: dict[str, Claim] = {"book": "booked", "move": "moved", "cancel": "cancelled"}

CATALOG_HEADER = "Reply facts (cite by id):"

# Đề xuất chờ vẫn còn chờ sau quyết định của lượt — cùng tập với `facts.py`.
_STILL_PENDING = frozenset({"kept", "clarify"})

# `TimeFact.role` -> vai trò dữ kiện (bước 1.1 brief).
_TIME_ROLE: dict[str, str] = {
    "free": "offer", "taken": "taken", "held": "held", "appointment": "appointment",
    "asked": "asked", "at": "asked", "shop_open": "hours", "shop_close": "hours",
    "busy_until": "busy",
}


class Decision(BaseModel):
    outcome: str
    kind: str
    at: Moment


def turn_decisions(turn_facts: list[TurnFact], pending_outcome: Optional[PendingOutcome]) -> list[Decision]:
    """Quyết định của lượt từ `DecisionFact`; `pending_outcome` chỉ là dự phòng
    khi `turn_facts` không có cái nào (lượt rơi về câu mẫu sau lỗi)."""
    found = [Decision(outcome=f.outcome, kind=f.proposal_kind, at=f.at)
             for f in turn_facts if isinstance(f, DecisionFact)]
    if not found and pending_outcome is not None:
        found = [Decision(outcome=pending_outcome.outcome, kind=pending_outcome.kind,
                          at=pending_outcome.at)]
    return found


async def services_by_id(catalog: ServiceCatalogService, ids: list[str]) -> dict[str, Service]:
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


def short_label(moment: Moment, today: date) -> str:
    """Nhãn giờ ngắn cho các mốc do CODE dựng (không phải lời khách): hôm nay /
    mai đổi thành "{giờ} hôm nay" / "{giờ} mai" thay vì nhắc lại cả ngày tháng
    đầy đủ ("Thứ Bảy 14/11, 3 giờ chiều") — model copy nguyên vào câu trả lời
    nghe cứng (live run 5). Ngày khác, hoặc iso không đọc được, giữ
    `moment.spoken` như cũ.
    """
    try:
        dt = datetime.fromisoformat(moment.iso)
    except ValueError:
        return moment.spoken
    local = to_local(dt)
    hhmm = format_vi_hhmm(f"{local.hour:02d}:{local.minute:02d}")
    if local.date() == today:
        return f"{hhmm} hôm nay"
    if local.date() == today + timedelta(days=1):
        return f"{hhmm} mai"
    return moment.spoken


def format_price(low: int, high: int) -> str:
    """120000, 180000 -> "120.000đ-180.000đ" — dấu chấm phân nghìn kiểu Việt."""
    def _vi(n: int) -> str:
        return f"{n:,}".replace(",", ".") + "đ"
    return f"{_vi(low)}-{_vi(high)}"


def _instant_of(moment: Moment) -> Optional[str]:
    """UTC ISO của một mốc giờ, dùng làm khoá gộp dữ kiện. Bỏ qua mốc không
    đọc được / không có múi giờ (naive) — không đoán múi giờ của nó."""
    try:
        dt = datetime.fromisoformat(moment.iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _outcome_role(outcome: str, kind: str, turn_id: str) -> tuple[Optional[str], list[str]]:
    """Vai trò + `announce_turn_ids` theo kết quả quyết định (bước 1.1/1.2
    brief) — dùng chung cho `DecisionFact` và `pending_outcome`."""
    if outcome == "approved":
        return CLAIM_FOR_KIND.get(kind), [turn_id]
    if outcome == "failed":
        return "failed", [turn_id]
    if outcome == "not_approvable":
        return "failed", []
    if outcome in ("rejected", "superseded"):
        return "released", []
    if outcome in _STILL_PENDING:
        return "held", []
    return None, []


class CatalogEntry(BaseModel):
    id: str
    kind: Literal["time", "service", "group", "customer_clock"]
    roles: list[str]
    label: str
    instant: Optional[str] = None
    clocks: list[tuple[int, int]] = []
    names: list[str] = []
    price: Optional[tuple[int, int]] = None
    service_ids: list[str] = []
    announce_turn_ids: list[str] = []


class FactCatalog(BaseModel):
    entries: list[CatalogEntry]

    def get(self, ref: str) -> Optional[CatalogEntry]:
        return next((entry for entry in self.entries if entry.id == ref), None)

    def id_of(self, moment: Moment) -> Optional[str]:
        instant = _instant_of(moment)
        if instant is None:
            return None
        return next((entry.id for entry in self.entries if entry.instant == instant), None)

    def render(self) -> str:
        lines = [f"- {entry.id} [{', '.join(entry.roles)}]: {entry.label}" for entry in self.entries]
        return "\n".join([CATALOG_HEADER, *(lines or ["- none"])])


class _TimeRecord:
    __slots__ = ("label", "roles", "clocks", "service_ids", "announce_turn_ids")

    def __init__(self, moment: Moment, today: date) -> None:
        self.label = short_label(moment, today)
        self.roles: list[str] = []
        self.clocks: list[tuple[int, int]] = sorted({key[:2] for key in clocks_of_moment(moment)})
        self.service_ids: list[str] = []
        self.announce_turn_ids: list[str] = []


def _dedupe(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


async def build_catalog(
    db: AsyncIOMotorDatabase, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
    pending_outcome: Optional[PendingOutcome], question: str, turn_id: str,
) -> FactCatalog:
    records: dict[str, _TimeRecord] = {}
    order: list[str] = []

    def add_time(moment: Moment, role: Optional[str], *, service_ids: Iterable[str] = (),
                 announce_turn_ids: Iterable[str] = ()) -> None:
        if role is None:
            return
        instant = _instant_of(moment)
        if instant is None:
            return
        if instant not in records:
            records[instant] = _TimeRecord(moment, snapshot.today)
            order.append(instant)
        record = records[instant]
        if role not in record.roles:
            record.roles.append(role)
        for service_id in service_ids:
            if service_id not in record.service_ids:
                record.service_ids.append(service_id)
        for ledger_turn_id in announce_turn_ids:
            if ledger_turn_id not in record.announce_turn_ids:
                record.announce_turn_ids.append(ledger_turn_id)

    proposals = [fact for fact in turn_facts if isinstance(fact, ProposalFact)]

    # -- 1. mốc giờ ------------------------------------------------------------
    for fact in turn_facts:
        if isinstance(fact, TimeFact):
            add_time(fact.moment, _TIME_ROLE.get(fact.role))
        elif isinstance(fact, ProposalFact):
            add_time(fact.at, "held", service_ids=fact.service_ids)
        elif isinstance(fact, DecisionFact):
            role, announce = _outcome_role(fact.outcome, fact.proposal_kind, turn_id)
            add_time(fact.at, role, announce_turn_ids=announce)

    if pending_outcome is not None:
        role, announce = _outcome_role(pending_outcome.outcome, pending_outcome.kind, turn_id)
        add_time(pending_outcome.at, role, announce_turn_ids=announce)
        for nearest in pending_outcome.nearest:
            add_time(nearest, "offer")

    decisions = turn_decisions(turn_facts, pending_outcome)
    if snapshot.pending is not None and all(d.outcome in _STILL_PENDING for d in decisions):
        add_time(snapshot.pending.at, "held", service_ids=snapshot.pending.service_ids)

    if snapshot.unannounced is not None:
        unannounced = snapshot.unannounced
        if unannounced.outcome == "approved":
            claim = CLAIM_FOR_KIND.get(unannounced.proposal_kind)
            add_time(unannounced.at, claim, announce_turn_ids=[unannounced.turn_id])
        elif unannounced.outcome == "failed":
            add_time(unannounced.at, "failed", announce_turn_ids=[unannounced.turn_id])

    for appointment in snapshot.upcoming:
        add_time(appointment.at, "appointment")

    for session in snapshot.sessions.values():
        for option in session.question.options:
            add_time(Moment(iso=option.iso, spoken=option.spoken), "offer")

    entries: list[CatalogEntry] = []
    for index, instant in enumerate(order, start=1):
        record = records[instant]
        entries.append(CatalogEntry(
            id=f"t{index}", kind="time", roles=record.roles, label=record.label, instant=instant,
            clocks=record.clocks, service_ids=record.service_ids,
            announce_turn_ids=record.announce_turn_ids,
        ))

    # -- 2. giờ tiệm -------------------------------------------------------------
    base = len(entries)
    for offset, (field, verb) in enumerate((("open_time", "opens"), ("close_time", "closes"))):
        hhmm = getattr(snapshot.shop_hours, field)
        entries.append(CatalogEntry(
            id=f"t{base + offset + 1}", kind="time", roles=["hours"],
            label=f"{verb} {format_vi_hhmm(hhmm)}", instant=f"hours:{hhmm}",
            clocks=sorted({key[:2] for key in clocks_of_hhmm(hhmm)}),
        ))

    # -- 3. giờ khách vừa nói ------------------------------------------------------
    seen_readings: list[frozenset] = []
    for reading in clocks_in_text(question):
        if reading in seen_readings:
            continue
        seen_readings.append(reading)
        clocks = sorted(reading)
        label = "customer said " + " or ".join(
            format_vi_hhmm(f"{hour:02d}:{minute:02d}") for hour, minute in clocks)
        entries.append(CatalogEntry(
            id=f"c{len(seen_readings)}", kind="customer_clock", roles=["asked"], label=label,
            clocks=clocks,
        ))

    # -- 4. dịch vụ ------------------------------------------------------------
    service_ids: list[str] = []
    for fact in turn_facts:
        if isinstance(fact, ServiceFact):
            service_ids.append(fact.service_id)
    service_ids += [fact.service_id for fact in snapshot.known_services]
    if snapshot.pending is not None:
        service_ids += list(snapshot.pending.service_ids)
    # Đề xuất vừa quyết định: sau khi duyệt nó hết `pending` (snapshot không
    # còn), dịch vụ của nó chỉ còn ở đây — câu "đã đặt" của kế hoạch nêu tên.
    if pending_outcome is not None:
        service_ids += list(pending_outcome.service_ids)
    for proposal in proposals:
        service_ids += list(proposal.service_ids)
    for appointment in snapshot.upcoming:
        service_ids += list(appointment.service_ids)
    for session in snapshot.sessions.values():
        memory_ids = session.memory.get("service_ids")
        if isinstance(memory_ids, list):
            service_ids += [sid for sid in memory_ids if isinstance(sid, str)]
    service_ids = _dedupe(service_ids)

    catalog = ServiceCatalogService(db)
    db_services = await services_by_id(catalog, service_ids)
    fact_by_id = {fact.service_id: fact for fact in turn_facts if isinstance(fact, ServiceFact)}

    name_by_id: dict[str, str] = {}
    service_count = 0
    for service_id in service_ids:
        if service_id in db_services:
            service = db_services[service_id]
            name, low, high = service.name, service.price_min, service.price_max
        elif service_id in fact_by_id:
            fact = fact_by_id[service_id]
            name, low, high = fact.name, fact.price_min, fact.price_max
        else:
            continue
        name_by_id[service_id] = name
        service_count += 1
        entries.append(CatalogEntry(
            id=f"s{service_count}", kind="service", roles=["service", "price"],
            label=f"{name}, {format_price(low, high)}", names=[name], price=(low, high),
            service_ids=[service_id],
        ))

    # -- 5. nhóm nhiều dịch vụ -----------------------------------------------------
    groups: list[list[str]] = []
    if snapshot.pending is not None:
        groups.append(list(snapshot.pending.service_ids))
    groups += [list(proposal.service_ids) for proposal in proposals]
    groups += [list(appointment.service_ids) for appointment in snapshot.upcoming]
    for session in snapshot.sessions.values():
        memory_ids = session.memory.get("service_ids")
        if isinstance(memory_ids, list):
            groups.append([sid for sid in memory_ids if isinstance(sid, str)])

    seen_groups: list[frozenset] = []
    group_count = 0
    for group in groups:
        unique = _dedupe(group)
        if len(unique) < 2 or not all(sid in name_by_id for sid in unique):
            continue
        key = frozenset(unique)
        if key in seen_groups:
            continue
        seen_groups.append(key)
        group_count += 1
        price = await catalog.price_range(unique)
        names = [name_by_id[sid] for sid in unique]
        entries.append(CatalogEntry(
            id=f"g{group_count}", kind="group", roles=["price"],
            label=f"{' + '.join(names)}, {format_price(*price)}", names=names, price=price,
            service_ids=unique,
        ))

    return FactCatalog(entries=entries)
