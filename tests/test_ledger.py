"""Task 1.3: `TurnLedger` (`turn_ledger`) — ghi trong vòng, đọc ở lượt sau (§6)."""
from __future__ import annotations

from datetime import timedelta

import pytest
import structlog

from app.agents.booking.context import PendingProposal, PreviousCall, PreviousTurn, UnannouncedOutcome
from app.agents.booking.ledger import LEDGER_COLLECTION, TurnLedger, previous_call_from
from app.agents.booking.schemas import (
    AppointmentList,
    AppointmentRefOut,
    CallRecord,
    FreeSlots,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
)
from app.core.clock import now_utc, to_local
from app.core.config import settings


def _moment(hours: int) -> Moment:
    dt = now_utc() + timedelta(hours=hours)
    return Moment(iso=dt.isoformat(), spoken=f"+{hours}h")


def _call(**overrides) -> CallRecord:
    defaults = dict(
        round=1,
        index=0,
        tool="appointment",
        action="find_slots",
        args_norm="{}",
        allowed=True,
        result=None,
        at=now_utc(),
    )
    defaults.update(overrides)
    return CallRecord(**defaults)


# ---------------------------------------------------------------------------
# start / append_call / finish — round-trip
# ---------------------------------------------------------------------------


async def test_start_append_finish_roundtrip(test_db):
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    call = _call(
        result=TaskResult(
            task_id="t1.0",
            agent="appointment",
            action="find_slots",
            status="ok",
            data=FreeSlots(day=to_local(asked_at).date(), slots=[_moment(1)], more=False, fetched_at=asked_at),
        ),
        at=asked_at,
    )

    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=asked_at)
    await ledger.append_call("t1", call)
    await ledger.finish("t1", ended_by="finish", reply_kind="answer")

    doc = await ledger.record_of("t1")
    assert doc is not None
    assert doc["user_id"] == "u1"
    assert doc["day"] == to_local(asked_at).date().isoformat()
    assert doc["ended_by"] == "finish"
    assert doc["reply_kind"] == "answer"
    assert len(doc["calls"]) == 1
    assert doc["calls"][0]["tool"] == "appointment"
    # datetime lưu dạng chuỗi json phải đọc lại ra được CallRecord tz-aware
    stored = CallRecord.model_validate(doc["calls"][0])
    assert stored.at.tzinfo is not None
    # Round-trip Mongo KHÔNG được làm `result` tuột về `dict` (smart union
    # chọn nhầm vì FreeSlots mang date/datetime đã hoá chuỗi JSON).
    assert isinstance(stored.result, TaskResult)
    assert stored.result.status == "ok"
    assert isinstance(stored.result.data, FreeSlots)


# ---------------------------------------------------------------------------
# previous_turns giữ nguyên kiểu của mọi biến thể TaskData + PendingOutcome
# qua round-trip Mongo thật (không rơi về fallback "failed"/dict).
# ---------------------------------------------------------------------------


def _resolved_time_data():
    return ResolvedTime(start_at=_moment(1), partial_date=None, missing=[], day_from=None)


def _free_slots_data():
    return FreeSlots(day=to_local(now_utc()).date(), slots=[_moment(1), _moment(2)], more=False, fetched_at=now_utc())


def _proposal_created_data():
    return ProposalCreated(proposal_id="p1", kind="book", start_at=_moment(3), note=None, replaces=None, queued=[])


def _appointment_list_data():
    return AppointmentList(appointments=[AppointmentRefOut(id="a1", at=_moment(1))])


def _shop_hours_data():
    return ShopHoursOut(open=_moment(0), close=_moment(10), closed_days=[])


def _shop_status_data():
    return ShopStatusOut(busy=False, until=None)


def _shop_open_data():
    return ShopOpenOut(at=_moment(0), open=True, reason="open", next_change=None)


@pytest.mark.parametrize(
    "make_result",
    [
        lambda: TaskResult(task_id="t", agent="appointment", action="resolve_time", status="ok", data=_resolved_time_data()),
        lambda: TaskResult(task_id="t", agent="appointment", action="find_slots", status="ok", data=_free_slots_data()),
        lambda: TaskResult(task_id="t", agent="appointment", action="propose_book", status="ok", data=_proposal_created_data()),
        lambda: TaskResult(task_id="t", agent="appointment", action="list_appointments", status="ok", data=_appointment_list_data()),
        lambda: TaskResult(task_id="t", agent="shop_info", action="get_hours", status="ok", data=_shop_hours_data()),
        lambda: TaskResult(task_id="t", agent="shop_info", action="get_status", status="ok", data=_shop_status_data()),
        lambda: TaskResult(task_id="t", agent="shop_info", action="is_open_at", status="ok", data=_shop_open_data()),
        lambda: PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="3h chiều"),
    ],
    ids=[
        "ResolvedTime", "FreeSlots", "ProposalCreated", "AppointmentList",
        "ShopHoursOut", "ShopStatusOut", "ShopOpenOut", "PendingOutcome",
    ],
)
async def test_previous_turns_keeps_typed_result_through_mongo_roundtrip(test_db, make_result):
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    today = to_local(asked_at).date()
    result = make_result()
    call = _call(result=result, at=asked_at)
    expected = previous_call_from(call)

    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=asked_at)
    await ledger.append_call("t1", call)

    turns = await ledger.previous_turns("u1", today=today)
    assert len(turns) == 1
    [actual] = turns[0].calls

    assert actual.status == expected.status
    assert actual.status != "failed"
    assert actual.offered == expected.offered
    assert actual.resolved == expected.resolved
    assert actual.proposal == expected.proposal


async def test_record_of_unknown_turn_is_none(test_db):
    ledger = TurnLedger(test_db)
    assert await ledger.record_of("no-such-turn") is None


async def test_set_pending_outcome_and_set_ask_persist(test_db):
    ledger = TurnLedger(test_db)
    asked_at = now_utc()
    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=asked_at)

    outcome = PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="3h chiều mai")
    await ledger.set_pending_outcome("t1", outcome)
    await ledger.set_ask("t1", "time")

    doc = await ledger.record_of("t1")
    assert doc["pending_outcome"]["outcome"] == "approved"
    assert doc["ask_field"] == "time"


# ---------------------------------------------------------------------------
# previous_turns
# ---------------------------------------------------------------------------


async def test_previous_turns_returns_at_most_two_most_recent_today_typed(test_db):
    ledger = TurnLedger(test_db)
    now = now_utc()
    today = to_local(now).date()

    # 3 lượt hôm nay, càng về sau càng mới; 1 lượt hôm qua.
    for i, offset_min in enumerate([30, 20, 10]):
        asked_at = now - timedelta(minutes=offset_min)
        turn_id = f"today-{i}"
        await ledger.start(turn_id=turn_id, user_id="u1", route="orchestrate", rule="R2", asked_at=asked_at)
        await ledger.append_call(
            turn_id,
            _call(result=TaskResult(task_id="x", agent="appointment", action="find_slots", status="ok"), at=asked_at),
        )
        await ledger.finish(turn_id, ended_by="finish", reply_kind="answer")

    yesterday_asked_at = now - timedelta(days=1)
    await ledger.start(turn_id="yesterday", user_id="u1", route="orchestrate", rule="R2", asked_at=yesterday_asked_at)
    await ledger.finish("yesterday", ended_by="finish", reply_kind="answer")

    turns = await ledger.previous_turns("u1", today=today, limit=2)

    assert [t.turn_id for t in turns] == ["today-2", "today-1"]
    assert all(isinstance(t, PreviousTurn) for t in turns)
    assert all(isinstance(c, PreviousCall) for c in turns[0].calls)


async def test_previous_turns_filters_by_user(test_db):
    ledger = TurnLedger(test_db)
    now = now_utc()
    today = to_local(now).date()
    await ledger.start(turn_id="mine", user_id="u1", route="orchestrate", rule="R2", asked_at=now)
    await ledger.start(turn_id="other", user_id="u2", route="orchestrate", rule="R2", asked_at=now)

    turns = await ledger.previous_turns("u1", today=today)
    assert [t.turn_id for t in turns] == ["mine"]


# ---------------------------------------------------------------------------
# previous_call_from — thuần, không DB
# ---------------------------------------------------------------------------


def test_previous_call_rejected_when_not_allowed():
    call = _call(allowed=False, reject_code="limit", result=None)
    pc = previous_call_from(call)
    assert pc.status == "rejected"
    assert pc.error_code == "limit"


def test_previous_call_pending_outcome_maps_to_ok():
    outcome = PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="3h chiều")
    call = _call(result=outcome)
    pc = previous_call_from(call)
    assert pc.status == "ok"
    assert pc.offered == []
    assert pc.resolved is None
    assert pc.proposal is None


@pytest.mark.parametrize("status", ["ok", "needs_info", "failed"])
def test_previous_call_task_result_status_passthrough(status):
    result = TaskResult(task_id="t", agent="appointment", action="find_slots", status=status, error_code="E" if status == "failed" else None)
    pc = previous_call_from(_call(result=result))
    assert pc.status == status
    assert pc.error_code == ("E" if status == "failed" else None)


def test_previous_call_maps_free_slots_to_offered():
    slots = [_moment(1), _moment(2)]
    result = TaskResult(task_id="t", agent="appointment", action="find_slots", status="ok", data=FreeSlots(day=to_local(now_utc()).date(), slots=slots, more=False, fetched_at=now_utc()))
    pc = previous_call_from(_call(result=result))
    assert pc.offered == slots


def test_previous_call_merges_nearest_into_offered():
    nearest = [_moment(3)]
    result = TaskResult(task_id="t", agent="appointment", action="propose_book", status="needs_info", nearest=nearest)
    pc = previous_call_from(_call(result=result))
    assert pc.offered == nearest


def test_previous_call_maps_resolved_time():
    moment = _moment(5)
    result = TaskResult(task_id="t", agent="appointment", action="resolve_time", status="ok", data=ResolvedTime(start_at=moment, partial_date=None, missing=[], day_from=None))
    pc = previous_call_from(_call(result=result))
    assert pc.resolved == moment


def test_previous_call_maps_proposal_created():
    moment = _moment(2)
    call_at = now_utc()
    result = TaskResult(
        task_id="t", agent="appointment", action="propose_book", status="ok",
        data=ProposalCreated(proposal_id="p1", kind="book", start_at=moment, note=None, replaces=None, queued=[]),
    )
    pc = previous_call_from(_call(result=result, at=call_at))
    assert isinstance(pc.proposal, PendingProposal)
    assert pc.proposal.id == "p1"
    assert pc.proposal.kind == "book"
    assert pc.proposal.spoken == moment.spoken
    assert pc.proposal.unclear_count == 0
    assert pc.proposal.target is None
    assert pc.proposal.expires_at == call_at


def test_previous_call_unexpected_result_shape_is_failed():
    pc = previous_call_from(_call(result=None))
    assert pc.status == "failed"


# ---------------------------------------------------------------------------
# unannounced / mark_announced
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,announced,expect_none",
    [
        ("kept", False, True),
        ("rejected", False, True),
        ("clarify", False, True),
        ("superseded", False, True),
        ("not_approvable", False, True),
        ("approved", True, True),
        ("failed", True, True),
        ("approved", False, False),
        ("failed", False, False),
    ],
)
async def test_unannounced_only_for_approved_or_failed_not_announced(test_db, outcome, announced, expect_none):
    ledger = TurnLedger(test_db)
    now = now_utc()
    today = to_local(now).date()
    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=now)
    po = PendingOutcome(outcome=outcome, proposal_id="p1", kind="book", spoken="3h chiều", error="x" if outcome == "failed" else None)
    await ledger.set_pending_outcome("t1", po)
    if announced:
        await ledger.mark_announced("t1")

    result = await ledger.unannounced("u1", today=today)
    if expect_none:
        assert result is None
    else:
        assert isinstance(result, UnannouncedOutcome)
        assert result.proposal_id == "p1"
        assert result.outcome == outcome
        # Fix round 1 Task 5.3: lượt nào cần `mark_announced` sau khi báo.
        assert result.turn_id == "t1"


async def test_unannounced_carries_the_appointment_iso(test_db):
    """Fix round 2 Task 5.3: mốc giờ THẬT của lịch vừa ghi đi theo
    `UnannouncedOutcome.iso` (guard cần nó để biết giờ của dòng R0)."""
    ledger = TurnLedger(test_db)
    now = now_utc()
    iso = "2026-11-07T15:00:00+07:00"
    await ledger.start(turn_id="t1", user_id="u1", route="button", rule="R1", asked_at=now)
    po = PendingOutcome(
        outcome="approved", proposal_id="p1", kind="book", spoken="3h chiều",
        appointment=AppointmentRefOut(id="a1", at=Moment(iso=iso, spoken="3h chiều")),
    )
    await ledger.set_pending_outcome("t1", po)

    result = await ledger.unannounced("u1", today=to_local(now).date())

    assert result is not None and result.iso == iso


async def test_mark_announced_clears_unannounced(test_db):
    ledger = TurnLedger(test_db)
    now = now_utc()
    today = to_local(now).date()
    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=now)
    await ledger.set_pending_outcome("t1", PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="3h chiều"))

    assert await ledger.unannounced("u1", today=today) is not None
    await ledger.mark_announced("t1")
    assert await ledger.unannounced("u1", today=today) is None


# ---------------------------------------------------------------------------
# TTL index
# ---------------------------------------------------------------------------


async def test_ttl_index_exists_after_first_start(test_db):
    ledger = TurnLedger(test_db)
    await ledger.start(turn_id="t1", user_id="u1", route="orchestrate", rule="R2", asked_at=now_utc())

    info = await test_db[LEDGER_COLLECTION].index_information()
    ttl_indexes = [idx for idx in info.values() if idx.get("key") == [("created_at", 1)]]
    assert ttl_indexes, info
    assert ttl_indexes[0]["expireAfterSeconds"] == settings.orch_ledger_ttl_days * 86400


# ---------------------------------------------------------------------------
# Gọi write trước khi `start()` -> KHÔNG upsert, chỉ cảnh báo (không bịa doc
# thiếu user_id/day).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,call_kwargs",
    [
        ("append_call", lambda ledger, turn_id: ledger.append_call(turn_id, _call())),
        ("set_pending_outcome", lambda ledger, turn_id: ledger.set_pending_outcome(
            turn_id, PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="x")
        )),
        ("set_ask", lambda ledger, turn_id: ledger.set_ask(turn_id, "time")),
        ("finish", lambda ledger, turn_id: ledger.finish(turn_id, ended_by="finish", reply_kind="answer")),
        ("mark_announced", lambda ledger, turn_id: ledger.mark_announced(turn_id)),
    ],
)
async def test_write_before_start_logs_warning_and_does_not_upsert(test_db, method, call_kwargs):
    ledger = TurnLedger(test_db)
    turn_id = "never-started"

    with structlog.testing.capture_logs() as logs:
        await call_kwargs(ledger, turn_id)

    warnings = [e for e in logs if e["event"] == "ledger_turn_missing"]
    assert len(warnings) == 1
    assert warnings[0]["extra"] == {"turn_id": turn_id, "method": method}
    assert await ledger.record_of(turn_id) is None
