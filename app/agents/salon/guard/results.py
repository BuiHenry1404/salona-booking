"""Business results của một lượt (spec guard rebuild, mục "Business results"):
`TurnResults` gom mọi dữ kiện CODE biết về lượt — không tin chữ model. Planner
(`planner.py`), câu mẫu (`templates.py`) và validator (`validator.py`) đều đọc
từ đây.

Nguồn: `TurnSnapshot` (DB), `turn_facts` (code trích từ ToolMessage),
`pending_outcome`, câu khách (chỉ để lấy giờ khách vừa nói), danh mục dịch vụ
(đọc DB một lần).

Hai kiểu giờ (Global Constraints):
- `ClockKey` (`clocks.py`, `(giờ, phút, buổi)`): giờ có thật của lượt, kiểm
  bằng `is_known` — `valid_clocks`.
- `Clock = (giờ, phút)`: giờ một kế hoạch bắt buộc / cấm nói.

Chỉ import `clocks.py` trong `guard/`. Không regex.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.salon.guard.clocks import (
    ClockKey,
    clock_of_24h,
    clocks_of_hhmm,
    clocks_of_moment,
    customer_clocks,
)
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
from app.core.config import settings
from app.core.errors import UnknownServiceError
from app.core.vi_format import format_vi_hhmm
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService

Clock = tuple[int, int]

# Kết quả quyết định, mỗi tập định nghĩa đúng một lần ở đây.
# Ghi thất bại — hành động `failed`.
FAILED_OUTCOMES = frozenset({"failed", "not_approvable"})
# Đề xuất bị bỏ trong lượt — hành động `released`.
RELEASED_OUTCOMES = frozenset({"rejected", "superseded"})
# Đề xuất chờ vẫn còn chờ sau quyết định của lượt.
STILL_PENDING_OUTCOMES = frozenset({"kept", "clarify"})

_MILLION = 1_000_000
_THOUSAND = 1_000


# ---------------------------------------------------------------------------
# nhãn giờ / giá nói
# ---------------------------------------------------------------------------


def short_label(moment: Moment, today: date) -> str:
    """Nhãn giờ ngắn cho các mốc do CODE dựng: hôm nay / mai thành
    "{giờ} hôm nay" / "{giờ} mai" thay vì cả ngày tháng ("Thứ Bảy 14/11,
    3 giờ chiều") — model chép nguyên vào câu nghe cứng (live run 5). Ngày
    khác, hoặc iso không đọc được, giữ `moment.spoken`."""
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


def _amount(value: int, *, unit: bool = True) -> str:
    """Một số tiền nói: 150000 -> "150 nghìn"; 1200000 -> "1 triệu 2";
    1250000 -> "1 triệu 250 nghìn"; 1000000 -> "1 triệu". `unit=False` bỏ
    chữ "nghìn" (vế đầu của khoảng dưới 1 triệu lấy đơn vị của vế sau —
    cách `scan_money` đọc "120 đến 150 nghìn")."""
    if value >= _MILLION:
        millions, rest = divmod(value, _MILLION)
        if rest == 0:
            return f"{millions} triệu"
        if rest % 100_000 == 0:
            return f"{millions} triệu {rest // 100_000}"
        if rest % _THOUSAND == 0:
            return f"{millions} triệu {rest // _THOUSAND} nghìn"
        return f"{value} đồng"
    if value % _THOUSAND == 0:
        thousands = value // _THOUSAND
        return f"{thousands} nghìn" if unit else str(thousands)
    return f"{value} đồng"


def spoken_price(low: int, high: int) -> str:
    """Khoảng giá nói: dưới 1 triệu "120 đến 150 nghìn" (chữ "đến", không gạch:
    `scan_money` đọc "120–150 nghìn" chỉ ra 150.000); từ 1 triệu "1 triệu 2
    đến 1 triệu 5". `low == high` -> một số."""
    if low == high:
        return _amount(low)
    shared_unit = high < _MILLION and low % _THOUSAND == 0 and high % _THOUSAND == 0
    return f"{_amount(low, unit=not shared_unit)} đến {_amount(high)}"


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class ServiceItem(BaseModel):
    service_id: str
    name: str
    price_min: int
    price_max: int
    # Từ `ServiceFact` của lượt: khách chọn dịch vụ này / khách hỏi giá nó.
    identified: bool = False
    asked_price: bool = False


class TurnResults(BaseModel):
    snapshot: TurnSnapshot
    turn_facts: list[TurnFact]
    pending_outcome: Optional[PendingOutcome]
    turn_id: str
    turn_kind: Literal["typed", "button"]
    # Mọi dịch vụ của lượt đã tra được trong danh mục — đúng tập
    # `turn_service_ids` (dịch vụ không còn trong danh mục thì bỏ).
    services: dict[str, ServiceItem]
    # Dịch vụ của lượt (spec "Dịch vụ của lượt"), theo thứ tự gặp, không trùng.
    turn_service_ids: list[str]
    # Tên mọi dịch vụ của tiệm — validator bắt tên dịch vụ không thuộc lượt.
    shop_service_names: list[str]
    # Mọi giờ có thật của lượt (kể cả giờ khách vừa nói).
    valid_clocks: set[ClockKey]
    shop_phone: Optional[str]
    previous_replies: list[str]
    # Có đề xuất chờ, lượt gõ chữ, chưa `decide_pending`.
    pending_undecided: bool

    @property
    def today(self) -> date:
        return self.snapshot.today

    @property
    def address(self) -> str:
        return self.snapshot.customer.address_phrase


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def instant_of(moment: Moment) -> Optional[str]:
    """UTC ISO của một mốc — khoá so hai mốc cùng thời điểm. Mốc không đọc được
    / không có múi giờ thì bỏ (không đoán múi giờ)."""
    try:
        dt = datetime.fromisoformat(moment.iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _dedupe(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _turn_service_ids(snapshot: TurnSnapshot, turn_facts: list[TurnFact],
                      pending_outcome: Optional[PendingOutcome]) -> list[str]:
    """Dịch vụ của lượt: `ServiceFact` của lượt; dịch vụ của đề xuất mới, đề
    xuất chờ, `pending_outcome`; dịch vụ của các lịch hẹn mà lượt kể (lịch
    sắp tới trùng thời điểm một `TimeFact` "appointment" của lượt). KHÔNG có
    `snapshot.known_services` của lượt trước, không có lịch lượt này không kể."""
    ids: list[str] = []
    for fact in turn_facts:
        if isinstance(fact, ServiceFact):
            ids.append(fact.service_id)
        elif isinstance(fact, ProposalFact):
            ids += fact.service_ids
    if snapshot.pending is not None:
        ids += snapshot.pending.service_ids
    if pending_outcome is not None:
        ids += pending_outcome.service_ids
    listed = {instant_of(fact.moment) for fact in turn_facts
              if isinstance(fact, TimeFact) and fact.role == "appointment"}
    listed.discard(None)
    for appointment in snapshot.upcoming:
        if instant_of(appointment.at) in listed:
            ids += appointment.service_ids
    return _dedupe(ids)


async def _services_by_id(catalog: ServiceCatalogService, ids: list[str]) -> dict[str, Service]:
    """Fail-soft như `shared/context.py::_service_names`: id không còn trong
    danh mục thì bỏ qua, không làm hỏng lượt."""
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


def _service_items(ids: list[str], db_services: dict[str, Service],
                   turn_facts: list[TurnFact]) -> dict[str, ServiceItem]:
    """Tên + giá đọc từ danh mục; id không còn trong danh mục nhưng có
    `ServiceFact` của lượt thì lấy từ fact. Cờ `identified` / `asked_price`
    lấy từ các `ServiceFact` của lượt."""
    facts = [fact for fact in turn_facts if isinstance(fact, ServiceFact)]
    items: dict[str, ServiceItem] = {}
    for service_id in ids:
        own = [fact for fact in facts if fact.service_id == service_id]
        if service_id in db_services:
            service = db_services[service_id]
            name, low, high = service.name, service.price_min, service.price_max
        elif own:
            name, low, high = own[0].name, own[0].price_min, own[0].price_max
        else:
            continue
        items[service_id] = ServiceItem(
            service_id=service_id, name=name, price_min=low, price_max=high,
            identified=any(fact.identified for fact in own),
            asked_price=any(fact.asked_price for fact in own),
        )
    return items


def _valid_clocks(snapshot: TurnSnapshot, turn_facts: list[TurnFact],
                  pending_outcome: Optional[PendingOutcome], question: str) -> set[ClockKey]:
    """Mọi giờ có thật của lượt: đề xuất chờ, lịch sắp tới, R0, giờ / đề xuất /
    quyết định của lượt, `pending_outcome` (gồm `nearest` — tool `decide_pending`
    đưa chúng cho model), lựa chọn của phiên chờ, giờ mở / đóng cửa, giờ khách
    vừa nói, giờ tiệm hết bận (khối bối cảnh đưa nó khi tiệm đang bận)."""
    moments: list[Optional[Moment]] = [
        snapshot.pending.at if snapshot.pending else None,
        snapshot.unannounced.at if snapshot.unannounced else None,
        *(appointment.at for appointment in snapshot.upcoming),
    ]
    for fact in turn_facts:
        if isinstance(fact, TimeFact):
            moments.append(fact.moment)
        elif isinstance(fact, (ProposalFact, DecisionFact)):
            moments.append(fact.at)
    if pending_outcome is not None:
        moments += [pending_outcome.at, *pending_outcome.nearest]
    for session in snapshot.sessions.values():
        moments += [Moment(iso=option.iso, spoken=option.spoken)
                    for option in session.question.options]
    clocks: set[ClockKey] = set()
    for moment in moments:
        clocks |= clocks_of_moment(moment)
    clocks |= clocks_of_hhmm(snapshot.shop_hours.open_time)
    clocks |= clocks_of_hhmm(snapshot.shop_hours.close_time)
    clocks |= customer_clocks(question)
    status = snapshot.shop_status
    if status.is_busy and status.busy_until is not None:
        local = to_local(status.busy_until)
        clocks.add(clock_of_24h(local.hour, local.minute))
    return clocks


# ---------------------------------------------------------------------------
# build_turn_results
# ---------------------------------------------------------------------------


async def build_turn_results(
    db: AsyncIOMotorDatabase, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
    pending_outcome: Optional[PendingOutcome], turn_id: str, turn_kind: Literal["typed", "button"],
    question: str, previous_replies: list[str], pending_decided: bool,
) -> TurnResults:
    """Dựng `TurnResults` cho lượt.

    `pending_decided` là cờ `pending_decided` của state: một `decide_pending`
    bị từ chối / bị veto vẫn để lại ToolMessage nhưng cờ vẫn `False`, nên
    `pending_undecided` đọc cờ này, không đọc lịch sử tool.
    """
    catalog = ServiceCatalogService(db)
    candidate_ids = _turn_service_ids(snapshot, turn_facts, pending_outcome)
    return turn_results_from(
        snapshot=snapshot, turn_facts=turn_facts, pending_outcome=pending_outcome,
        turn_id=turn_id, turn_kind=turn_kind, question=question,
        previous_replies=previous_replies, pending_decided=pending_decided,
        catalog_services=await _services_by_id(catalog, candidate_ids),
        shop_service_names=[service.name for service in await catalog.list()],
        shop_phone=settings.shop_phone or None,
    )


def turn_results_from(
    *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
    pending_outcome: Optional[PendingOutcome], turn_id: str, turn_kind: Literal["typed", "button"],
    question: str, previous_replies: list[str], pending_decided: bool,
    catalog_services: dict[str, Service], shop_service_names: list[str],
    shop_phone: Optional[str],
) -> TurnResults:
    """Phần không đọc DB của `build_turn_results`: `catalog_services` là các
    dịch vụ đã tra được theo id (thừa id không sao — chỉ dịch vụ của lượt được
    đọc). Test dựng `TurnResults` không cần DB qua hàm này."""
    candidate_ids = _turn_service_ids(snapshot, turn_facts, pending_outcome)
    services = _service_items(candidate_ids, catalog_services, turn_facts)
    return TurnResults(
        snapshot=snapshot,
        turn_facts=list(turn_facts),
        pending_outcome=pending_outcome,
        turn_id=turn_id,
        turn_kind=turn_kind,
        services=services,
        turn_service_ids=[service_id for service_id in candidate_ids if service_id in services],
        shop_service_names=list(shop_service_names),
        valid_clocks=_valid_clocks(snapshot, turn_facts, pending_outcome, question),
        shop_phone=shop_phone,
        previous_replies=list(previous_replies),
        pending_undecided=(turn_kind == "typed" and snapshot.pending is not None
                           and not pending_decided),
    )
