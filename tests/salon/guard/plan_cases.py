"""Mọi lượt mà `test_planner.py` dựng kế hoạch (guard rebuild, Task 5).

`RESULTS[name]` là `TurnResults` của ca (dựng không cần DB, `turn_results`);
`PLAN_CASES` là `(name, results, build_plan(results))` cho từng ca — Task 7
import để chứng minh câu mẫu của mọi kế hoạch qua validator.
"""
from __future__ import annotations

from app.agents.salon.guard.planner import ReplyPlan, build_plan
from app.agents.salon.guard.results import TurnResults
from tests.salon.guard.builders import (
    CAT,
    GEL,
    TODAY,
    TURN_ID,
    a_session,
    a_snapshot,
    appointment_at,
    at,
    decision_fact,
    outcome,
    pending_at,
    proposal_fact,
    r0,
    service_fact,
    time_fact,
    turn_results,
)

SHOP_PHONE = "0912345678"


def _opened(**kwargs):
    return {kwargs.get("agent", "appointment"): a_session(opened_turn_id=TURN_ID, **kwargs)}


def _failed(*, nearest=()) -> dict:
    return {"turn_facts": [decision_fact(at(15), outcome="failed")],
            "pending_outcome": outcome(at(15), outcome="failed", nearest=nearest),
            "shop_phone": SHOP_PHONE}


_DONE = {"turn_facts": [decision_fact(at(16))],
         "pending_outcome": outcome(at(16), service_ids=[GEL.id])}


RESULTS: dict[str, TurnResults] = {
    "taken_with_session_offer": turn_results(
        snapshot=a_snapshot(sessions=_opened(options=[at(16), at(17)])),
        turn_facts=[time_fact("taken", at(15))]),
    "proposal_drops_offer": turn_results(
        turn_facts=[proposal_fact(at(16), service_ids=[GEL.id]),
                    time_fact("free", at(17)), time_fact("free", at(18))]),
    "cancel_proposal": turn_results(
        turn_facts=[proposal_fact(at(16), kind="cancel", service_ids=[GEL.id])]),
    "proposal_with_confirm_session": turn_results(
        snapshot=a_snapshot(sessions=_opened(fields=["confirm"])),
        turn_facts=[proposal_fact(at(16), service_ids=[GEL.id])]),
    "five_free": turn_results(
        turn_facts=[time_fact("free", at(hour)) for hour in (9, 10, 11, 13, 14)]),
    "asked_price_on_booking": turn_results(
        turn_facts=[proposal_fact(at(16), service_ids=[GEL.id]),
                    service_fact(GEL, asked_price=True)]),
    "identified_no_booking": turn_results(turn_facts=[service_fact(GEL, identified=True)]),
    "identified_with_proposal": turn_results(
        turn_facts=[proposal_fact(at(16), service_ids=[GEL.id]),
                    service_fact(GEL, identified=True)]),
    "done_with_services": turn_results(**_DONE),
    "r0_same_turn": turn_results(
        snapshot=a_snapshot(unannounced=r0(at(16)).model_copy(update={"turn_id": TURN_ID})),
        **_DONE),
    "r0_other_turn": turn_results(snapshot=a_snapshot(unannounced=r0(at(11)))),
    "failed_no_nearest": turn_results(**_failed()),
    "failed_with_nearest": turn_results(**_failed(nearest=[at(16), at(17)])),
    "failed_nearest_with_free": turn_results(
        **{**_failed(nearest=[at(16), at(17)]),
           "turn_facts": [decision_fact(at(15), outcome="failed"),
                          time_fact("free", at(16)), time_fact("free", at(17))]}),
    "failed_twice": turn_results(
        snapshot=a_snapshot(unannounced=r0(at(11), outcome="failed")), **_failed()),
    "released": turn_results(turn_facts=[decision_fact(at(11), outcome="rejected")],
                             pending_outcome=outcome(at(11), outcome="rejected")),
    "kept_pending": turn_results(
        snapshot=a_snapshot(pending=pending_at(at(11), service_ids=[CAT.id])),
        turn_facts=[decision_fact(at(11), outcome="kept")], pending_decided=True),
    "clarify_pending": turn_results(
        snapshot=a_snapshot(pending=pending_at(at(11), service_ids=[CAT.id])),
        turn_facts=[decision_fact(at(11), outcome="clarify")], pending_decided=True),
    "ask_combined": turn_results(snapshot=a_snapshot(sessions=_opened(fields=["day", "time"]))),
    "ask_with_options": turn_results(
        snapshot=a_snapshot(
            upcoming=[appointment_at(at(9), "a-1"), appointment_at(at(14), "a-2")],
            sessions=_opened(fields=["which_appointment"], options=[at(9), at(14)]))),
    # Giờ kín trùng giờ mở cửa (review F3): câu nêu giờ mở cửa từ khối bối
    # cảnh không được bị `taken_mentioned` — kế hoạch có hành động `hours`.
    "taken_at_shop_open": turn_results(
        turn_facts=[time_fact("taken", at(8)), time_fact("free", at(9))]),
    "hours": turn_results(turn_facts=[time_fact("shop_open", at(8)),
                                      time_fact("shop_close", at(20))]),
    "busy": turn_results(turn_facts=[time_fact("busy_until", at(16, 30, day=TODAY))]),
    "appointments": turn_results(
        turn_facts=[time_fact("appointment", at(hour)) for hour in (9, 10, 11, 13, 14, 15)]),
    "button_turn": turn_results(turn_kind="button", **_DONE),
    "greeting": turn_results(),
}

PLAN_CASES: list[tuple[str, TurnResults, ReplyPlan]] = [
    (name, results, build_plan(results)) for name, results in RESULTS.items()
]
