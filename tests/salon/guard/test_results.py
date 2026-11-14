"""`guard/results.py`: the turn's facts in one model (guard rebuild, Task 4)."""
from __future__ import annotations

from datetime import date

from app.agents.salon.guard.clocks import is_known
from app.agents.salon.guard.results import short_label, spoken_price
from app.agents.salon.shared.text_scan import scan_money
from app.core.config import settings
from tests.salon.guard.builders import (
    CAT,
    GEL,
    GOI,
    TODAY,
    UON,
    a_session,
    a_snapshot,
    appointment_at,
    at,
    outcome,
    pending_at,
    proposal_fact,
    r0,
    results_for,
    seed_catalog,
    service_fact,
    time_fact,
)


def _knows(results, hour: int, minute: int = 0) -> bool:
    return is_known(frozenset({(hour, minute)}), results.valid_clocks)


async def test_turn_services_are_this_turn_only(test_db):
    await seed_catalog(test_db)
    snapshot = a_snapshot(known_services=[service_fact(GEL, identified=True)],
                          upcoming=[appointment_at(at(10), services=[GEL])])
    results = await results_for(test_db, snapshot=snapshot,
                                turn_facts=[service_fact(CAT, identified=True)])
    assert results.turn_service_ids == [CAT.id]
    assert GEL.id not in results.services
    item = results.services[CAT.id]
    assert (item.name, item.price_min, item.price_max, item.identified, item.asked_price) == (
        "Cắt tóc nữ", 100000, 150000, True, False)


async def test_turn_services_include_proposal_pending_outcome_and_listed_appointments(test_db):
    await seed_catalog(test_db)
    snapshot = a_snapshot(
        pending=pending_at(at(11), service_ids=[CAT.id]),
        upcoming=[appointment_at(at(10), "a-1", services=[UON]),
                  appointment_at(at(17), "a-2", services=[GEL])],
    )
    results = await results_for(
        test_db, snapshot=snapshot,
        turn_facts=[proposal_fact(at(14), service_ids=[GOI.id]), time_fact("appointment", at(10))],
        pending_outcome=outcome(at(9), outcome="kept", service_ids=[CAT.id]),
    )
    assert set(results.turn_service_ids) == {GOI.id, CAT.id, UON.id}
    assert set(results.services) == {GOI.id, CAT.id, UON.id}
    assert results.services[UON.id].name == "Uốn tóc"


async def test_pending_outcome_services_join_the_turn(test_db):
    await seed_catalog(test_db)
    results = await results_for(test_db, pending_outcome=outcome(at(9), service_ids=[GEL.id]))
    assert results.turn_service_ids == [GEL.id]


async def test_valid_clocks_include_context_and_customer_times(test_db):
    snapshot = a_snapshot(
        pending=pending_at(at(11)),
        upcoming=[appointment_at(at(10))],
        unannounced=r0(at(17)),
        sessions={"appointment": a_session(options=[at(13, 30)])},
    )
    results = await results_for(
        test_db, snapshot=snapshot, turn_facts=[time_fact("free", at(9))],
        pending_outcome=outcome(at(14), outcome="failed", nearest=[at(16)]),
        question="3h chiều được không em",
    )
    for hour, minute in [(9, 0), (8, 0), (19, 0), (10, 0), (11, 0), (13, 30), (14, 0), (16, 0),
                         (17, 0), (15, 0)]:
        assert _knows(results, hour, minute), (hour, minute)
    assert not _knows(results, 12, 15)


async def test_pending_undecided(test_db):
    snapshot = a_snapshot(pending=pending_at(at(11)))
    assert (await results_for(test_db, snapshot=snapshot)).pending_undecided is True
    assert (await results_for(test_db, snapshot=snapshot, turn_kind="button")).pending_undecided is False
    assert (await results_for(test_db, snapshot=snapshot, pending_decided=True)).pending_undecided is False
    assert (await results_for(test_db)).pending_undecided is False


async def test_results_carry_shop_and_customer_facts(test_db, monkeypatch):
    await seed_catalog(test_db)
    monkeypatch.setattr(settings, "shop_phone", "")
    results = await results_for(test_db, previous=["a", "b"])
    assert {"Sơn gel", "Gội sấy", "Cắt tóc nữ", "Uốn tóc"} <= set(results.shop_service_names)
    assert results.shop_phone is None
    assert (results.address, results.today, results.previous_replies) == ("chị Lan", TODAY, ["a", "b"])
    monkeypatch.setattr(settings, "shop_phone", "0901234567")
    assert (await results_for(test_db)).shop_phone == "0901234567"


async def test_asked_price_flag_comes_from_the_turn_fact(test_db):
    await seed_catalog(test_db)
    results = await results_for(test_db, turn_facts=[service_fact(GEL, asked_price=True)])
    assert (results.services[GEL.id].identified, results.services[GEL.id].asked_price) == (False, True)


def test_spoken_price():
    assert spoken_price(120_000, 150_000) == "120 đến 150 nghìn"
    assert spoken_price(1_200_000, 1_500_000) == "1 triệu 2 đến 1 triệu 5"
    assert spoken_price(150_000, 150_000) == "150 nghìn"
    assert spoken_price(1_000_000, 1_250_000) == "1 triệu đến 1 triệu 250 nghìn"
    assert spoken_price(800_000, 1_200_000) == "800 nghìn đến 1 triệu 2"
    for low, high in [(120_000, 150_000), (1_200_000, 1_500_000), (1_000_000, 1_250_000),
                      (800_000, 1_200_000), (50_000, 80_000)]:
        assert scan_money(spoken_price(low, high)) == [low, high], (low, high)
    assert scan_money(spoken_price(150_000, 150_000)) == [150_000]


def test_short_label():
    assert short_label(at(15, day=TODAY, spoken="unused"), TODAY) == "3 giờ chiều hôm nay"
    assert short_label(at(15, spoken="unused"), TODAY) == "3 giờ chiều mai"
    other = at(9, day=date(2026, 11, 12), spoken="Thứ Năm 12/11, 9 giờ sáng")
    assert short_label(other, TODAY) == "Thứ Năm 12/11, 9 giờ sáng"
