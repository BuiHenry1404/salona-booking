"""Guard accuracy set (guard rebuild, Task 10; spec "Bộ đo độ chính xác"):
real bot replies from live runs 2–5 (`accuracy/cases.json`), each with the
turn's facts rebuilt by hand in minimal form, run through `build_plan` +
`validate` — in the style of Slot Error Rate measurement:

- `bad` replies must be blocked with at least the listed codes (a leak fails);
- `ok` replies blocked by mistake (false blocks) may not exceed
  `max_false_blocks` (starts at 0);
- `accepted_leak` replies are wrong in a way the spec accepts the validator
  cannot see ("booked" claim on a proposal time, wrong form of address): they
  must pass, and are only counted.

`cases.json` holds BOT text only. A case's `turn`:
  `times`    [{"role", "at"}]           — `TimeFact`s of the turn
  `services` [{"id", "identified"?, "asked_price"?}] — `ServiceFact`s
  `proposal` {"at", "kind"?, "services"?} — new `ProposalFact`
  `decision` {"outcome", "at", "kind"?, "services"?, "nearest"?}
             — `DecisionFact` + `pending_outcome` (marks the pending decided)
  `pending`  {"at", "kind"?, "services"?} — the snapshot's pending proposal
  `upcoming` [{"at", "services"?}]       — the customer's upcoming appointments
  `session`  {"fields", "options"?, "opened_this_turn"?} — appointment session
  `address`  the context block's address (default "chị Lan")
  `customer_times` ["HH:MM"] — times the customer just said (only the times
             are kept, spoken by `format_vi_hhmm`; never the customer's text)
  `known_services` [id]   — services identified in EARLIER turns of the
             conversation (`snapshot.known_services`; not this turn's services)
  `previous_replies` [str] — the bot's earlier replies (for `repeat`)
  `agents` [name]          — subagents that ran this turn (`AgentStatusFact`)
Times are "+D HH:MM": D days after the run's `today` (`run_days`); spoken
like the live runs ("Thứ Năm 12/11, 3 giờ chiều"). Services are ids of
`catalog` (the live runs' catalogue, `data/mock/services.json`).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.agents.salon.guard.planner import build_plan
from app.agents.salon.guard.results import TurnResults, turn_results_from
from app.agents.salon.guard.validator import CODES, validate
from app.agents.salon.shared.context import CustomerView, TurnSnapshot
from app.agents.salon.shared.schemas import AgentStatusFact, Moment
from app.core.clock import TZ
from app.core.vi_format import format_vi_datetime, format_vi_hhmm
from app.models.service import Service
from app.models.shop import ShopHours, ShopStatusView
from tests.salon.guard.builders import (
    a_session,
    appointment_at,
    decision_fact,
    outcome,
    pending_at,
    proposal_fact,
    service_fact,
    time_fact,
)
from tests.salon.guard.plan_cases import SHOP_PHONE

CASES_PATH = Path(__file__).parent / "accuracy" / "cases.json"
DATA: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
CATALOG: dict[str, Service] = {row["id"]: Service(**row) for row in DATA["catalog"]}
LABELS = ("ok", "bad", "accepted_leak")
TURN_ID = "turn-accuracy"


def _moment(spec: str, today: date) -> Moment:
    offset, hhmm = spec.split()
    hour, minute = (int(part) for part in hhmm.split(":"))
    day = today + timedelta(days=int(offset))
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return Moment(iso=dt.isoformat(), spoken=format_vi_datetime(dt))


def results_of(case: dict) -> TurnResults:
    today = date.fromisoformat(DATA["run_days"][case["run"]])
    turn = case["turn"]

    def m(spec: str) -> Moment:
        return _moment(spec, today)

    facts: list = [time_fact(t["role"], m(t["at"])) for t in turn.get("times", [])]
    facts += [service_fact(CATALOG[s["id"]], identified=s.get("identified", False),
                           asked_price=s.get("asked_price", False))
              for s in turn.get("services", [])]
    facts += [AgentStatusFact(agent=agent, status="ok") for agent in turn.get("agents", [])]
    pending_outcome = None
    if "proposal" in turn:
        p = turn["proposal"]
        facts.append(proposal_fact(m(p["at"]), kind=p.get("kind", "book"),
                                   service_ids=p.get("services", [])))
    if "decision" in turn:
        d = turn["decision"]
        kind = d.get("kind", "book")
        facts.append(decision_fact(m(d["at"]), outcome=d["outcome"], kind=kind))
        pending_outcome = outcome(m(d["at"]), outcome=d["outcome"], kind=kind,
                                  nearest=[m(n) for n in d.get("nearest", [])],
                                  service_ids=d.get("services", []))
    pending = turn.get("pending")
    sessions = {}
    if "session" in turn:
        s = turn["session"]
        sessions["appointment"] = a_session(
            fields=s["fields"], options=[m(o) for o in s.get("options", [])],
            opened_turn_id=TURN_ID if s.get("opened_this_turn") else "turn-before")
    now = datetime(today.year, today.month, today.day, 10, 0, tzinfo=TZ)
    snapshot = TurnSnapshot(
        now_local=now, today=today, calendar_14d=[],
        customer=CustomerView(id="u-1", display_name="Lan",
                              address_phrase=turn.get("address", "chị Lan")),
        shop_hours=ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
        shop_status=ShopStatusView(is_busy=False),
        upcoming=[appointment_at(m(a["at"]), f"a-{i}",
                                 services=[CATALOG[sid] for sid in a.get("services", [])])
                  for i, a in enumerate(turn.get("upcoming", []))],
        pending=(None if pending is None
                 else pending_at(m(pending["at"]), kind=pending.get("kind", "book"),
                                 service_ids=pending.get("services", []))),
        unannounced=None, sessions=sessions,
        known_services=[service_fact(CATALOG[sid], identified=True)
                        for sid in turn.get("known_services", [])],
    )
    return turn_results_from(
        snapshot=snapshot, turn_facts=facts, pending_outcome=pending_outcome, turn_id=TURN_ID,
        turn_kind="typed",
        question=" ".join(format_vi_hhmm(t) for t in turn.get("customer_times", [])),
        previous_replies=list(turn.get("previous_replies", [])),
        pending_decided="decision" in turn, catalog_services=CATALOG,
        shop_service_names=[service.name for service in CATALOG.values()],
        shop_phone=SHOP_PHONE,
    )


def _codes(case: dict) -> list[str]:
    results = results_of(case)
    return validate(case["reply"], build_plan(results), results).codes


def test_cases_are_well_formed():
    ids = [case["id"] for case in DATA["cases"]]
    assert len(ids) == len(set(ids))
    for case in DATA["cases"]:
        assert case["label"] in LABELS, case["id"]
        assert case["run"] in DATA["run_days"], case["id"]
        assert set(case.get("codes", [])) <= set(CODES), case["id"]
        assert bool(case.get("codes")) == (case["label"] == "bad"), case["id"]
    labels = {case["label"] for case in DATA["cases"]}
    assert labels == set(LABELS)


def test_accuracy_set():
    false_blocks, leaks, accepted, accepted_blocked = [], [], [], []
    counts = {label: 0 for label in LABELS}
    for case in DATA["cases"]:
        label = case["label"]
        counts[label] += 1
        codes = _codes(case)
        if label == "ok" and codes:
            false_blocks.append((case["id"], codes))
        elif label == "bad" and not set(case["codes"]) <= set(codes):
            leaks.append((case["id"], case["codes"], codes))
        elif label == "accepted_leak":
            (accepted_blocked if codes else accepted).append((case["id"], codes))
    print(f"\nguard accuracy: false blocks {len(false_blocks)}/{counts['ok']} ok, "
          f"leaks {len(leaks)}/{counts['bad']} bad, "
          f"accepted leaks {len(accepted)}/{counts['accepted_leak']}")
    assert not leaks, f"bad replies not blocked with the listed codes: {leaks}"
    assert not accepted_blocked, f"accepted_leak replies must pass: {accepted_blocked}"
    assert len(false_blocks) <= DATA["max_false_blocks"], f"ok replies blocked: {false_blocks}"
