"""Helper dựng dữ liệu cho test của guard mới (results, planner, templates,
validator) — dựng bằng tay, chỉ đọc DB cho danh mục dịch vụ (`seed_catalog`).

Hôm nay của snapshot là 09/11/2026; mọi `at(...)` mặc định nằm ở 10/11/2026
("mai", trừ khi truyền `day=`), nên `short_label` ra "3 giờ chiều mai".
`spoken` mặc định là cụm giờ ngắn do `format_vi_hhmm` dựng ("3 giờ chiều").

`turn_results(...)` dựng `TurnResults` không cần DB (danh mục là `CATALOG`)
— cho các ca phải có sẵn lúc import (`plan_cases.PLAN_CASES`).

Mặc định là một lượt gõ chữ bình thường; mọi helper nhận tham số khoá để các
task sau đổi từng phần.

Tách khỏi `tests/salon/guard/__init__.py` vì file đó còn import guard cũ
tới Task 8.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal, Optional, Sequence

from app.agents.salon.guard.results import (
    TurnResults,
    build_turn_results,
    turn_results_from,
)
from app.agents.salon.shared.context import AppointmentView, CustomerView, TurnSnapshot
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    PendingView,
    ProposalFact,
    ServiceFact,
    TimeFact,
    UnannouncedDecision,
)
from app.core.clock import TZ, now_utc
from app.core.vi_format import format_vi_hhmm
from app.models.service import Service
from app.models.shop import ShopHours, ShopStatusView
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.service_catalog import ServiceCatalogService

TURN_ID = "turn-1"
TODAY = date(2026, 11, 9)
DAY = date(2026, 11, 10)

GEL = Service(id="nail-gel", category="nail", name="Sơn gel", price_min=120000,
              price_max=150000, duration_minutes=45)
GOI = Service(id="toc-goi-say", category="toc", name="Gội sấy", price_min=50000,
              price_max=80000, duration_minutes=30)
CAT = Service(id="toc-cat-nu", category="toc", name="Cắt tóc nữ", price_min=100000,
              price_max=150000, duration_minutes=45)
UON = Service(id="toc-uon", category="toc", name="Uốn tóc", price_min=1200000,
              price_max=1500000, duration_minutes=120)
CATALOG = (GEL, GOI, CAT, UON)


# -- moments ------------------------------------------------------------------


def at(hour: int, minute: int = 0, *, day: date = DAY, spoken: Optional[str] = None) -> Moment:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return Moment(iso=dt.isoformat(), spoken=spoken or format_vi_hhmm(f"{hour:02d}:{minute:02d}"))


# -- turn facts ---------------------------------------------------------------


def time_fact(role: str, moment: Moment) -> TimeFact:
    return TimeFact(role=role, moment=moment)


def service_fact(service: Service, *, identified: bool = False, asked_price: bool = False) -> ServiceFact:
    return ServiceFact(service_id=service.id, name=service.name, price_min=service.price_min,
                       price_max=service.price_max, duration_minutes=service.duration_minutes,
                       identified=identified, asked_price=asked_price)


def proposal_fact(moment: Moment, *, kind: str = "book", service_ids: Sequence[str] = (),
                  proposal_id: str = "p-new") -> ProposalFact:
    return ProposalFact(proposal_id=proposal_id, proposal_kind=kind, at=moment,
                        service_ids=list(service_ids))


def decision_fact(moment: Moment, *, outcome: str = "approved", kind: str = "book") -> DecisionFact:
    return DecisionFact(outcome=outcome, proposal_kind=kind, at=moment)


def outcome(moment: Moment, *, outcome: str = "approved", kind: str = "book",
            nearest: Sequence[Moment] = (), service_ids: Sequence[str] = (),
            proposal_id: str = "p-pending") -> PendingOutcome:
    return PendingOutcome(outcome=outcome, proposal_id=proposal_id, kind=kind, at=moment,
                          nearest=list(nearest), service_ids=list(service_ids))


# -- snapshot pieces ----------------------------------------------------------


def pending_at(moment: Moment, *, kind: str = "book", service_ids: Sequence[str] = ()) -> PendingView:
    return PendingView(id="p-pending", kind=kind, at=moment,
                       expires_at=now_utc() + timedelta(minutes=10),
                       service_ids=list(service_ids), unclear_count=0)


def appointment_at(moment: Moment, appt_id: str = "a-1", *,
                   services: Sequence[Service] = ()) -> AppointmentView:
    return AppointmentView(id=appt_id, at=moment, service_names=[s.name for s in services],
                           service_ids=[s.id for s in services])


def r0(moment: Moment, *, outcome: str = "approved", kind: str = "book") -> UnannouncedDecision:
    return UnannouncedDecision(turn_id="turn-0", outcome=outcome, proposal_kind=kind, at=moment)


def a_session(*, agent: str = "appointment", fields: Sequence[str] = ("time",),
              opened_turn_id: str = "turn-0", options: Sequence[Moment] = (),
              service_ids: Sequence[str] = ()) -> WaitingSession:
    asked = now_utc()
    return WaitingSession(
        user_id="u-1", agent=agent, task="Book", memory={"service_ids": list(service_ids)},
        question=SessionQuestion(fields=list(fields),
                                 options=[SessionOption(**m.model_dump()) for m in options],
                                 text="?"),
        asked_at=asked, expires_at=asked + timedelta(minutes=10), opened_turn_id=opened_turn_id,
    )


def a_snapshot(*, address: str = "chị Lan", pending: Optional[PendingView] = None,
               unannounced: Optional[UnannouncedDecision] = None,
               upcoming: Sequence[AppointmentView] = (),
               sessions: Optional[dict[str, WaitingSession]] = None,
               known_services: Sequence[ServiceFact] = (),
               shop_hours: Optional[ShopHours] = None,
               shop_status: Optional[ShopStatusView] = None) -> TurnSnapshot:
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=TZ)
    return TurnSnapshot(
        now_local=now, today=now.date(), calendar_14d=[],
        customer=CustomerView(id="u-1", display_name="Lan", address_phrase=address),
        shop_hours=shop_hours or ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
        shop_status=shop_status or ShopStatusView(is_busy=False),
        upcoming=list(upcoming), pending=pending, unannounced=unannounced,
        sessions=sessions or {}, known_services=list(known_services),
    )


# -- catalogue + results ------------------------------------------------------


async def seed_catalog(db, services: Sequence[Service] = CATALOG) -> None:
    await ServiceCatalogService(db).upsert_many(list(services))


async def results_for(db, *, snapshot: Optional[TurnSnapshot] = None, turn_facts: Sequence = (),
                      turn_kind: Literal["typed", "button"] = "typed",
                      pending_outcome: Optional[PendingOutcome] = None, question: str = "",
                      previous: Sequence[str] = (), pending_decided: bool = False,
                      turn_id: str = TURN_ID) -> TurnResults:
    return await build_turn_results(
        db, snapshot=snapshot or a_snapshot(), turn_facts=list(turn_facts),
        pending_outcome=pending_outcome, turn_id=turn_id, turn_kind=turn_kind, question=question,
        previous_replies=list(previous), pending_decided=pending_decided,
    )


def turn_results(*, snapshot: Optional[TurnSnapshot] = None, turn_facts: Sequence = (),
                 turn_kind: Literal["typed", "button"] = "typed",
                 pending_outcome: Optional[PendingOutcome] = None, question: str = "",
                 previous: Sequence[str] = (), pending_decided: bool = False,
                 turn_id: str = TURN_ID, shop_phone: Optional[str] = None) -> TurnResults:
    """Như `results_for` nhưng không đọc DB: danh mục là `CATALOG`."""
    return turn_results_from(
        snapshot=snapshot or a_snapshot(), turn_facts=list(turn_facts),
        pending_outcome=pending_outcome, turn_id=turn_id, turn_kind=turn_kind, question=question,
        previous_replies=list(previous), pending_decided=pending_decided,
        catalog_services={service.id: service for service in CATALOG},
        shop_service_names=[service.name for service in CATALOG], shop_phone=shop_phone,
    )
