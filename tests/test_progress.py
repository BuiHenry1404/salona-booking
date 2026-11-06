"""Task 5.1: `progress.py` — `step_goal` (§7.2) và `turn_facts` (§8.3).

Hàm thuần: không LLM, không DB. `OrchestrateOutcome`/`CallRecord` dựng tay —
`args_norm` qua `normalize_args` thật (`orchestrator/guard.py`) để JSON đúng
hình dạng guard sinh ra trong vận hành thật.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agents.booking.orchestrator.agent import OrchestrateOutcome
from app.agents.booking.orchestrator.guard import normalize_args
from app.agents.booking.progress import step_goal, turn_facts
from app.agents.booking.resolver import ResolveResult
from app.agents.booking.schemas import (
    AskCustomerArgs,
    CallRecord,
    FinishArgs,
    PendingOutcome,
)
from app.core.config import settings
from app.models.conversation import GoalProgress

from tests.factories import make_ctx

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def _call(
    tool: str,
    *,
    action: str | None = None,
    args: dict | None = None,
    allowed: bool = True,
    markers: list[str] | None = None,
    round_: int = 1,
) -> CallRecord:
    raw = dict(args or {})
    if action is not None:
        raw = {"action": action, "args": raw}
    return CallRecord(
        round=round_,
        index=0,
        tool=tool,
        action=action,
        args_norm=normalize_args(tool, raw),
        allowed=allowed,
        markers=markers or [],
        at=NOW,
    )


def _appt_call(service=None, appointment_id=None, markers=None, action="propose_book") -> CallRecord:
    return _call(
        "appointment_task",
        action=action,
        args={"time": None, "service": service, "appointment_id": appointment_id, "for_whom": None, "also": None},
        markers=markers,
    )


def _decide_call(aside: str | None, markers=None) -> CallRecord:
    return _call("decide_pending", args={"label": "approve", "aside": aside, "reason": "x"}, markers=markers)


def _outcome(
    calls: list[CallRecord],
    *,
    pending_outcome: PendingOutcome | None = None,
    ended_by: str = "finish",
    finish: FinishArgs | None = None,
    ask: AskCustomerArgs | None = None,
) -> OrchestrateOutcome:
    return OrchestrateOutcome(
        calls=calls,
        pending_outcome=pending_outcome,
        ended_by=ended_by,
        finish=finish if finish is not None else (FinishArgs(kind_hint="answer", service=None, stated=[]) if ended_by == "finish" else None),
        ask=ask,
        proposals_created=0,
        rounds_executed=1,
    )


def _resolve(rule="R5") -> ResolveResult:
    return ResolveResult(route="orchestrate", rule=rule, must_announce=False)


def _pending_outcome(outcome: str) -> PendingOutcome:
    return PendingOutcome(outcome=outcome, proposal_id="p1", kind="book", spoken="9h sáng mai")


# ---------------------------------------------------------------------------
# step_goal — bảng §7.2
# ---------------------------------------------------------------------------


def test_first_appointment_call_opens_a_goal():
    goal = GoalProgress()
    outcome = _outcome([_appt_call(service="gội đầu", action="resolve_time")], ended_by="ask_customer",
                        ask=AskCustomerArgs(field="day", hint=None))
    ctx = make_ctx(goal=goal, open_question=None)

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.goal_id is not None
    assert new_goal.service == "gội đầu"
    assert limit_hit is False


def test_social_turn_neither_counts_nor_resets():
    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome([], ended_by="finish")  # finish(kind_hint="social"/"unsupported") — không call nào

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal == goal
    assert limit_hit is False


def test_shop_only_turn_is_neutral():
    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome([_call("shop_info_task", action="get_hours", args={"time": None})], ended_by="finish")

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal == goal
    assert limit_hit is False


def test_defer_kept_is_neutral():
    """`decide_pending(aside="defer_with_request")` giữ nguyên đề xuất (`kept`)
    — khách gửi thêm việc khác, mục tiêu hiện tại KHÔNG đổi: không đếm, không
    reset, không đóng. Khác `defer_only` (xem `test_approved_closes_goal`)."""
    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome(
        [_decide_call("defer_with_request")], pending_outcome=_pending_outcome("kept"), ended_by="finish",
    )

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal == goal
    assert limit_hit is False


def test_no_marker_turn_counts():
    goal = GoalProgress(goal_id="g1", turns_without_progress=0, service="gội đầu")
    ctx = make_ctx(goal=goal, open_question=None)
    outcome = _outcome([_appt_call(service="gội đầu", action="resolve_time")], ended_by="finish")

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.turns_without_progress == 1
    assert new_goal.goal_id == "g1"
    assert limit_hit is False


def test_first_ask_for_a_new_field_does_not_count():
    from app.agents.booking.context import OpenQuestion

    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal, open_question=OpenQuestion(field="day", asked_at=NOW, times=1))
    outcome = _outcome(
        [_appt_call(service="gội đầu", action="resolve_time")],
        ended_by="ask_customer",
        ask=AskCustomerArgs(field="time", hint=None),
    )

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.turns_without_progress == 0
    assert limit_hit is False


def test_repeat_ask_same_field_counts():
    from app.agents.booking.context import OpenQuestion

    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal, open_question=OpenQuestion(field="time", asked_at=NOW, times=1))
    outcome = _outcome(
        [_appt_call(service="gội đầu", action="resolve_time")],
        ended_by="ask_customer",
        ask=AskCustomerArgs(field="time", hint=None),
    )

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.turns_without_progress == 2
    assert limit_hit is False


def test_marker_resets_count():
    goal = GoalProgress(goal_id="g1", turns_without_progress=2, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome(
        [_appt_call(service="gội đầu", action="resolve_time", markers=["time_resolved"])],
        ended_by="finish",
    )

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.turns_without_progress == 0
    assert new_goal.last_marker == "time_resolved"
    assert limit_hit is False


@pytest.mark.parametrize(
    "pending_outcome,aside",
    [("approved", None), ("rejected", None), ("kept", "defer_only")],
    ids=["approved", "rejected", "kept_defer_only"],
)
def test_approved_closes_goal(pending_outcome, aside):
    """Đóng mục tiêu (§7.2): `approved`, `rejected` dứt khoát, và
    `decide_pending(aside="defer_only")` (khác `defer_with_request`, xem
    `test_defer_kept_is_neutral`)."""
    goal = GoalProgress(goal_id="g1", turns_without_progress=2, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome([_decide_call(aside)], pending_outcome=_pending_outcome(pending_outcome), ended_by="finish")

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal == GoalProgress()
    assert limit_hit is False


def test_service_change_opens_a_new_goal():
    goal = GoalProgress(goal_id="g1", turns_without_progress=1, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome([_appt_call(service="làm móng", action="propose_book")], ended_by="finish")

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert new_goal.goal_id is not None
    assert new_goal.goal_id != "g1"
    assert new_goal.service == "làm móng"
    assert new_goal.turns_without_progress == 0
    assert limit_hit is False


def test_third_strike_sets_limit_and_resets():
    goal = GoalProgress(goal_id="g1", turns_without_progress=settings.orch_goal_turns_limit - 1, service="gội đầu")
    ctx = make_ctx(goal=goal)
    outcome = _outcome([_appt_call(service="gội đầu", action="resolve_time")], ended_by="finish")

    new_goal, limit_hit = step_goal(goal, outcome, _resolve(), ctx)

    assert limit_hit is True
    assert new_goal.turns_without_progress == 0
    assert new_goal.goal_id == "g1"


# ---------------------------------------------------------------------------
# turn_facts — §8.3
# ---------------------------------------------------------------------------


def test_turn_facts_caps_stated_at_two_and_cleans():
    finish = FinishArgs(kind_hint="answer", service="gội đầu", stated=["thích chị Lan cắt", "ghét đợi lâu", "bỏ"])
    outcome = _outcome([], ended_by="finish", finish=finish)
    ctx = make_ctx()

    facts = turn_facts(outcome, _resolve(), ctx)

    assert facts.service == "gội đầu"
    assert len(facts.stated) == 2
    assert facts.stated == ["thích chị Lan cắt", "ghét đợi lâu"]


def test_turn_facts_handles_none_outcome():
    ctx = make_ctx()
    facts = turn_facts(None, ResolveResult(route="button", rule="R1", must_announce=False), ctx)

    assert facts.service is None
    assert facts.stated == []
    assert facts.answers_question is False


@pytest.mark.parametrize(
    "rule,ask,open_field,expected",
    [
        ("R3", None, "time", True),
        ("R3", "day", "time", True),
        ("R3", "time", "time", False),
        ("R5", None, "time", False),
    ],
    ids=["r3_no_ask", "r3_ask_other_field", "r3_ask_same_field", "not_r3"],
)
def test_answers_question_only_on_r3_without_same_field_ask(rule, ask, open_field, expected):
    from app.agents.booking.context import OpenQuestion

    ctx = make_ctx(open_question=OpenQuestion(field=open_field, asked_at=NOW, times=1))
    ask_args = AskCustomerArgs(field=ask, hint=None) if ask is not None else None
    ended_by = "ask_customer" if ask is not None else "finish"
    outcome = _outcome([], ended_by=ended_by, ask=ask_args)

    facts = turn_facts(outcome, _resolve(rule), ctx)

    assert facts.answers_question is expected
