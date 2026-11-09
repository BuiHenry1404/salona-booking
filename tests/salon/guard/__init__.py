"""Helper dùng chung cho test guard (Task 12): dựng `TurnSnapshot` giả bằng tay
(không đọc DB), danh mục dịch vụ mẫu, và gọi `build_reply_facts` với các mặc
định của một lượt gõ chữ bình thường.

Mọi mốc giờ nằm ở ngày cố định 10/11/2026 — guard không so ngày, chỉ so giờ,
nên không cần ngày tương đối. `spoken` mặc định là cụm giờ ngắn
("3 giờ chiều", do `format_vi_hhmm` dựng) để các câu trong bảng của task brief
chứa nguyên văn nó (`missing_fact` so nguyên văn — xem
`test_must_mention_is_verbatim`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from app.agents.salon.guard.facts import ReplyFacts, build_reply_facts
from app.agents.salon.shared.context import AppointmentView, CustomerView, TurnSnapshot
from app.agents.salon.shared.schemas import Moment, PendingOutcome, PendingView, UnannouncedDecision
from app.core.clock import TZ, now_utc
from app.core.vi_format import format_vi_hhmm
from app.models.service import Service
from app.models.shop import ShopHours, ShopStatusView
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.service_catalog import ServiceCatalogService

TURN_ID = "turn-1"
PHONE = "0901234567"
DAY = date(2026, 11, 10)

GEL = Service(id="nail-gel", category="nail", name="Sơn gel", price_min=120000,
              price_max=180000, duration_minutes=45)
GOI = Service(id="toc-goi-say", category="toc", name="Gội sấy", price_min=50000,
              price_max=80000, duration_minutes=30)


def at(hour: int, minute: int = 0, *, spoken: Optional[str] = None) -> Moment:
    dt = datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=TZ)
    return Moment(iso=dt.isoformat(), spoken=spoken or format_vi_hhmm(f"{hour:02d}:{minute:02d}"))


def pending_at(moment: Moment, *, kind: str = "book", service_ids: Sequence[str] = ()) -> PendingView:
    return PendingView(id="p-pending", kind=kind, at=moment,
                       expires_at=now_utc() + timedelta(minutes=10),
                       service_ids=list(service_ids), unclear_count=0)


def appointment_at(moment: Moment, appt_id: str = "a-1") -> AppointmentView:
    return AppointmentView(id=appt_id, at=moment, service_names=[])


def r0(moment: Moment, *, outcome: str = "approved", kind: str = "book") -> UnannouncedDecision:
    return UnannouncedDecision(turn_id="turn-0", outcome=outcome, proposal_kind=kind, at=moment)


def a_session(*, agent: str = "appointment", field: str = "time", opened_turn_id: str = "turn-0",
              options: Sequence[Moment] = (), service_ids: Sequence[str] = ()) -> WaitingSession:
    asked = now_utc()
    return WaitingSession(
        user_id="u-1", agent=agent, task="Book", memory={"service_ids": list(service_ids)},
        question=SessionQuestion(field=field, options=[SessionOption(**m.model_dump()) for m in options],
                                 text="?"),
        asked_at=asked, expires_at=asked + timedelta(minutes=10), opened_turn_id=opened_turn_id,
    )


def a_snapshot(*, address: str = "chị Lan", pending: Optional[PendingView] = None,
               unannounced: Optional[UnannouncedDecision] = None,
               upcoming: Sequence[AppointmentView] = (),
               sessions: Optional[dict[str, WaitingSession]] = None) -> TurnSnapshot:
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    return TurnSnapshot(
        now_local=now, today=now.date(), calendar_14d=[],
        customer=CustomerView(id="u-1", display_name="Lan", address_phrase=address),
        shop_hours=ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
        shop_status=ShopStatusView(is_busy=False),
        upcoming=list(upcoming), pending=pending, unannounced=unannounced,
        sessions=sessions or {}, known_services=[],
    )


async def seed_catalog(db) -> None:
    await ServiceCatalogService(db).upsert_many([GEL, GOI])


async def facts_for(db, *, snapshot: Optional[TurnSnapshot] = None, turn_facts: Sequence = (),
                    turn_kind: str = "typed", pending_outcome: Optional[PendingOutcome] = None,
                    question: str = "", previous: Sequence[str] = (),
                    pending_decided: bool = False) -> ReplyFacts:
    return await build_reply_facts(
        db, snapshot=snapshot or a_snapshot(), turn_facts=list(turn_facts), turn_id=TURN_ID,
        turn_kind=turn_kind, pending_outcome=pending_outcome, question=question,
        previous_replies=list(previous), pending_decided=pending_decided,
    )
