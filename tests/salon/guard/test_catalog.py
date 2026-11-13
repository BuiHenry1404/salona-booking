"""Task 1: `FactCatalog` — dữ kiện lượt đánh số id + vai trò, cho câu trả lời
cuối trích dẫn (spec level C §2-§3). Test ở đây dựng trực tiếp `turn_facts` /
`snapshot` / `pending_outcome`, không gọi `build_reply_facts` — hai cái đọc
cùng nguồn nhưng kiểm hai thứ khác nhau.
"""
import pytest

from app.agents.salon.guard.catalog import CatalogEntry, FactCatalog
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
    UnannouncedDecision,
)
from tests.salon.guard import (
    GEL,
    GOI,
    TURN_ID,
    a_session,
    a_snapshot,
    appointment_at,
    at,
    catalog_for,
    pending_at,
    r0,
    seed_catalog,
)
from app.services.service_catalog import ServiceCatalogService


def _times(cat: FactCatalog) -> list[CatalogEntry]:
    return [e for e in cat.entries if e.kind == "time" and e.instant and not e.instant.startswith("hours")]


def _hours(cat: FactCatalog) -> list[CatalogEntry]:
    return [e for e in cat.entries if e.kind == "time" and e.instant and e.instant.startswith("hours")]


async def test_same_instant_merges_roles(test_db):
    cat = await catalog_for(test_db, turn_facts=[TimeFact(role="asked", moment=at(15)),
                                                 TimeFact(role="taken", moment=at(15))])
    [t] = _times(cat)
    assert (t.id, t.roles, t.label, t.clocks) == ("t1", ["asked", "taken"], "3 giờ chiều", [(15, 0)])


async def test_free_time_and_session_option_are_offer(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[TimeFact(role="free", moment=at(13))],
        snapshot=a_snapshot(sessions={"appointment": a_session(options=[at(14)])}),
    )
    times = _times(cat)
    assert [(t.id, t.roles) for t in times] == [("t1", ["offer"]), ("t2", ["offer"])]


async def test_new_proposal_is_held_with_its_services(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[ProposalFact(proposal_id="p1", proposal_kind="book", at=at(16),
                                         service_ids=["nail-gel"])],
    )
    [t] = _times(cat)
    assert (t.roles, t.service_ids) == (["held"], ["nail-gel"])


async def test_pending_is_held_only_while_undecided(test_db):
    approved = await catalog_for(
        test_db, snapshot=a_snapshot(pending=pending_at(at(15))),
        turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=at(15))],
    )
    [t] = _times(approved)
    assert (t.roles, t.announce_turn_ids) == (["booked"], [TURN_ID])

    kept = await catalog_for(
        test_db, snapshot=a_snapshot(pending=pending_at(at(15))),
        turn_facts=[DecisionFact(outcome="kept", proposal_kind="book", at=at(15))],
    )
    [t] = _times(kept)
    assert (t.roles, t.announce_turn_ids) == (["held"], [])


async def test_r0_is_announced_with_its_own_turn(test_db):
    approved = await catalog_for(test_db, snapshot=a_snapshot(unannounced=r0(at(9))))
    [t] = _times(approved)
    assert (t.roles, t.announce_turn_ids) == (["booked"], ["turn-0"])

    failed = await catalog_for(test_db, snapshot=a_snapshot(unannounced=r0(at(9), outcome="failed")))
    [t] = _times(failed)
    assert (t.roles, t.announce_turn_ids) == (["failed"], ["turn-0"])


async def test_rejected_and_superseded_decisions_are_released(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[DecisionFact(outcome="rejected", proposal_kind="book", at=at(10)),
                             DecisionFact(outcome="superseded", proposal_kind="move", at=at(11))],
    )
    times = _times(cat)
    assert [t.roles for t in times] == [["released"], ["released"]]


async def test_upcoming_appointment_role(test_db):
    cat = await catalog_for(test_db, snapshot=a_snapshot(upcoming=[appointment_at(at(12))]))
    [t] = _times(cat)
    assert t.roles == ["appointment"]


async def test_shop_hours_entries(test_db):
    cat = await catalog_for(test_db)
    hours = _hours(cat)
    assert [(h.id, h.roles, h.label) for h in hours] == [
        ("t1", ["hours"], "opens 8 giờ sáng"),
        ("t2", ["hours"], "closes 7 giờ tối"),
    ]


async def test_customer_clock_has_both_readings(test_db):
    cat = await catalog_for(test_db, question="3 giờ nha em")
    [c] = [e for e in cat.entries if e.kind == "customer_clock"]
    assert (c.id, c.clocks, c.label) == ("c1", [(3, 0), (15, 0)], "customer said 03:00 or 15:00")

    none = await catalog_for(test_db, question="")
    assert [e for e in none.entries if e.kind == "customer_clock"] == []


async def test_services_come_from_facts_then_catalog(test_db):
    await seed_catalog(test_db)
    cat = await catalog_for(
        test_db,
        turn_facts=[ServiceFact(service_id=GEL.id, name=GEL.name, price_min=GEL.price_min,
                               price_max=GEL.price_max, duration_minutes=GEL.duration_minutes)],
        snapshot=a_snapshot(pending=pending_at(at(15), service_ids=[GOI.id])),
    )
    services = [e for e in cat.entries if e.kind == "service"]
    assert [(s.id, s.label, s.roles) for s in services] == [
        ("s1", "Sơn gel, 120.000đ-180.000đ", ["service", "price"]),
        ("s2", "Gội sấy, 50.000đ-80.000đ", ["service", "price"]),
    ]


async def test_group_price_for_a_multi_service_pending(test_db):
    await seed_catalog(test_db)
    cat = await catalog_for(
        test_db, snapshot=a_snapshot(pending=pending_at(at(15), service_ids=[GEL.id, GOI.id])),
    )
    [g] = [e for e in cat.entries if e.kind == "group"]
    assert g.roles == ["price"]
    assert g.price == await ServiceCatalogService(test_db).price_range([GEL.id, GOI.id])


def test_render_lists_ids_roles_and_labels():
    cat = FactCatalog(entries=[
        CatalogEntry(id="t1", kind="time", roles=["taken"], label="Thứ Năm 12/11, 3 giờ chiều"),
        CatalogEntry(id="s1", kind="service", roles=["service", "price"], label="Sơn gel, 120.000đ-180.000đ"),
    ])
    assert cat.render() == ("Reply facts (cite by id):\n- t1 [taken]: Thứ Năm 12/11, 3 giờ chiều\n"
                            "- s1 [service, price]: Sơn gel, 120.000đ-180.000đ")


def test_render_with_no_entries():
    assert FactCatalog(entries=[]).render() == "Reply facts (cite by id):\n- none"


async def test_catalog_is_deterministic(test_db):
    await seed_catalog(test_db)
    kwargs = dict(
        snapshot=a_snapshot(pending=pending_at(at(15), service_ids=[GEL.id])),
        turn_facts=[TimeFact(role="asked", moment=at(10))],
    )
    first = await catalog_for(test_db, **kwargs)
    second = await catalog_for(test_db, **kwargs)
    assert first == second


async def test_id_of_finds_a_moment_by_instant(test_db):
    cat = await catalog_for(test_db, turn_facts=[TimeFact(role="asked", moment=at(15))])
    assert cat.id_of(at(15)) == "t1"


async def test_nearest_free_is_offer_even_with_a_decision_fact(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=at(15))],
        pending_outcome=PendingOutcome(outcome="failed", proposal_id="p1", kind="book", at=at(15),
                                       nearest=[at(16), at(17)]),
    )
    by_hour = {t.clocks[0][0]: t.roles for t in _times(cat)}
    assert by_hour == {15: ["failed"], 16: ["offer"], 17: ["offer"]}


async def test_busy_and_at_roles(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[TimeFact(role="busy_until", moment=at(18)), TimeFact(role="at", moment=at(9))],
    )
    by_hour = {t.clocks[0][0]: t.roles for t in _times(cat)}
    assert by_hour == {18: ["busy"], 9: ["asked"]}


async def test_announce_ids_stay_unique(test_db):
    cat = await catalog_for(
        test_db, turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=at(9))],
        snapshot=a_snapshot(unannounced=UnannouncedDecision(turn_id=TURN_ID, outcome="approved",
                                                            proposal_kind="book", at=at(9))),
    )
    [t] = _times(cat)
    assert t.announce_turn_ids == [TURN_ID]
    assert cat.id_of(Moment(iso=at(9).iso, spoken="khác")) == "t1"
