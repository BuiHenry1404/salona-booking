"""`guard/planner.py`: acts + ask + taken times as the plan JSON (guard rebuild, Task 5).

Every turn lives in `plan_cases.RESULTS`, so `PLAN_CASES` covers every plan built here.
"""
from __future__ import annotations

import json
import re

from app.agents.salon.guard.planner import build_plan
from tests.salon.guard.builders import TURN_ID
from tests.salon.guard.plan_cases import PLAN_CASES, RESULTS, SHOP_PHONE


def _plan(name: str):
    return build_plan(RESULTS[name])


def _json(name: str) -> dict:
    return _plan(name).to_json()


_PROPOSAL = {"act": "proposal", "kind": "book", "at": "4 giờ chiều mai", "services": ["Sơn gel"]}


def test_taken_goes_to_code_not_acts():
    assert _json("taken_with_session_offer") == {
        "taken_told_by_code": ["3 giờ chiều mai"],
        "acts": [{"act": "offer", "times": ["4 giờ chiều mai", "5 giờ chiều mai"]}],
        "ask": None,
    }
    plan = _plan("taken_with_session_offer")
    assert plan.taken_clocks == [(15, 0)]
    assert plan.act_clocks() == {(16, 0), (17, 0)}


def test_proposal_drops_offer():
    assert _json("proposal_drops_offer") == {"taken_told_by_code": [], "acts": [_PROPOSAL],
                                             "ask": None}
    act = _plan("proposal_drops_offer").acts[0]
    assert (act.clocks, act.service_names) == ([(16, 0)], ["Sơn gel"])


def test_cancel_proposal_has_no_services():
    assert _json("cancel_proposal")["acts"] == [
        {"act": "proposal", "kind": "cancel", "at": "4 giờ chiều mai", "services": []}]
    assert _plan("cancel_proposal").acts[0].service_names == []


def test_proposal_with_confirm_session_asks_once():
    plan = _plan("proposal_with_confirm_session")
    assert plan.to_json() == {"taken_told_by_code": [], "acts": [_PROPOSAL], "ask": None}
    assert plan.ask is None
    assert plan.asked_session == "appointment"


def test_offer_at_most_three():
    assert _json("five_free") == {
        "taken_told_by_code": [],
        "acts": [{"act": "offer", "times": ["9 giờ sáng mai", "10 giờ sáng mai", "11 giờ sáng mai"]}],
        "ask": None,
    }
    assert _plan("five_free").asked_session is None


def test_asked_price_on_a_booking_turn():
    assert _json("asked_price_on_booking")["acts"] == [
        _PROPOSAL, {"act": "price", "service": "Sơn gel", "price": "120 đến 150 nghìn"}]
    price = _plan("asked_price_on_booking").acts[1]
    assert (price.prices, price.service_names) == ([(120000, 150000)], ["Sơn gel"])


def test_identified_priced_only_without_booking_act():
    assert _json("identified_no_booking") == {
        "taken_told_by_code": [],
        "acts": [{"act": "price", "service": "Sơn gel", "price": "120 đến 150 nghìn"}],
        "ask": None,
    }
    assert _json("identified_with_proposal")["acts"] == [_PROPOSAL]


def test_done_with_services_and_announce():
    assert _json("done_with_services") == {
        "taken_told_by_code": [],
        "acts": [{"act": "done", "kind": "book", "at": "4 giờ chiều mai", "services": ["Sơn gel"]}],
        "ask": None,
    }
    assert _plan("done_with_services").announce_turn_ids() == [TURN_ID]


def test_r0_same_as_turn_decision_not_twice():
    assert _json("r0_same_turn") == _json("done_with_services")
    assert _plan("r0_same_turn").announce_turn_ids() == [TURN_ID]


def test_r0_of_an_earlier_turn_is_announced():
    assert _json("r0_other_turn")["acts"] == [
        {"act": "done", "kind": "book", "at": "11 giờ sáng mai", "services": []}]
    assert _plan("r0_other_turn").announce_turn_ids() == ["turn-0"]


def test_failed_phone_rule():
    plan = _plan("failed_no_nearest")
    assert plan.to_json() == {
        "taken_told_by_code": [],
        "acts": [{"act": "failed", "at": "3 giờ chiều mai", "nearest": [], "phone": SHOP_PHONE}],
        "ask": None,
    }
    assert plan.phone == SHOP_PHONE
    assert plan.announce_turn_ids() == [TURN_ID]

    plan = _plan("failed_with_nearest")
    assert plan.to_json()["acts"] == [{"act": "failed", "at": "3 giờ chiều mai",
                                       "nearest": ["4 giờ chiều mai", "5 giờ chiều mai"],
                                       "phone": None}]
    assert plan.phone is None
    assert plan.act_clocks() == {(15, 0), (16, 0), (17, 0)}

    plan = _plan("failed_twice")
    assert plan.to_json()["acts"] == [
        {"act": "failed", "at": "3 giờ chiều mai", "nearest": [], "phone": SHOP_PHONE},
        {"act": "failed", "at": "11 giờ sáng mai", "nearest": [], "phone": None},
    ]
    assert plan.phone == SHOP_PHONE
    assert plan.announce_turn_ids() == [TURN_ID, "turn-0"]


def test_failed_nearest_drops_free_offer():
    assert _json("failed_nearest_with_free")["acts"] == [
        {"act": "failed", "at": "3 giờ chiều mai",
         "nearest": ["4 giờ chiều mai", "5 giờ chiều mai"], "phone": None}]


def test_released():
    assert _json("released")["acts"] == [{"act": "released", "kind": "book", "at": "11 giờ sáng mai"}]
    assert _plan("released").announce_turn_ids() == []


def test_kept_pending_not_reasked_clarify_reasked():
    assert _json("kept_pending") == {"taken_told_by_code": [], "acts": [], "ask": None}
    assert _json("clarify_pending")["acts"] == [
        {"act": "proposal", "kind": "book", "at": "11 giờ sáng mai", "services": ["Cắt tóc nữ"]}]


def test_ask_combines_fields():
    plan = _plan("ask_combined")
    assert plan.to_json() == {"taken_told_by_code": [], "acts": [],
                              "ask": {"fields": ["day", "time"], "options": []}}
    assert plan.asked_session == "appointment"
    assert plan.ask is not None and plan.ask.agent == "appointment"
    assert not plan.is_empty()


def test_ask_carries_non_time_options():
    plan = _plan("ask_with_options")
    assert plan.to_json()["ask"] == {"fields": ["which_appointment"],
                                     "options": ["9 giờ sáng mai", "2 giờ chiều mai"]}
    assert plan.acts == []
    assert plan.asked_session == "appointment"


def test_offer_from_opened_session_replaces_ask():
    plan = _plan("taken_with_session_offer")
    assert plan.ask is None
    assert plan.asked_session == "appointment"


def test_hours_busy_appointments():
    assert _json("hours")["acts"] == [{"act": "hours", "open": "8 giờ sáng", "close": "8 giờ tối"}]
    assert _plan("hours").act_clocks() == {(8, 0), (20, 0)}
    assert _json("busy")["acts"] == [{"act": "busy", "until": "4 giờ rưỡi chiều hôm nay"}]
    assert _json("appointments")["acts"] == [{"act": "appointments", "times": [
        "9 giờ sáng mai", "10 giờ sáng mai", "11 giờ sáng mai", "1 giờ chiều mai",
        "2 giờ chiều mai"]}]


def test_button_turn_has_no_decision_act():
    plan = _plan("button_turn")
    assert plan.to_json() == {"taken_told_by_code": [], "acts": [], "ask": None}
    assert plan.announce_turn_ids() == []


def test_greeting_plan_is_empty():
    plan = _plan("greeting")
    assert plan.is_empty()
    assert plan.to_json() == {"taken_told_by_code": [], "acts": [], "ask": None}
    assert plan.phone is None and plan.asked_session is None


def test_plan_cases_cover_every_turn():
    assert [name for name, _, _ in PLAN_CASES] == list(RESULTS)
    for name, results, plan in PLAN_CASES:
        assert plan == build_plan(results), name


def test_json_has_no_clock_digits():
    for name, _, plan in PLAN_CASES:
        text = json.dumps(plan.to_json(), ensure_ascii=False)
        json.loads(text)
        assert not re.search(r"\d:\d", text), (name, text)
        assert not re.search(r"\d{4}-\d{2}-\d{2}", text), (name, text)


def test_old_guard_is_gone_and_turn_decisions_has_one_copy():
    """Task 8: the old guard (catalog, facts, plan, checks, fallback, segments)
    is deleted, so `planner._turn_decisions` is the only copy of the old
    `catalog.turn_decisions`."""
    import importlib.util
    from pathlib import Path

    from app.agents.salon.guard import planner

    for name in ("catalog", "facts", "plan", "checks", "fallback", "segments"):
        assert importlib.util.find_spec(f"app.agents.salon.guard.{name}") is None, name
    package = Path(planner.__file__).parent.parent
    copies = [path for path in package.rglob("*.py") if "def _turn_decisions" in path.read_text(
        encoding="utf-8") or "def turn_decisions" in path.read_text(encoding="utf-8")]
    assert copies == [Path(planner.__file__)]
