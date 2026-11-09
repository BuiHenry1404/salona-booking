"""TDD cho `TurnLedger` (`salon_turns`, Task 6) — collection mới, không chung
với `turn_ledger` cũ. `TurnLedger` không biết gì về `User` thật: `user_id`/
`thread_id` chỉ là chuỗi, nên test dùng uuid thẳng, không dựng user DB."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.agents.salon.shared.schemas import (
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
)
from app.agents.salon.turn.ledger import LEDGER_COLLECTION, TurnLedger
from app.core.clock import now_utc, to_local


def _moment(hour: int = 9) -> Moment:
    at = now_utc().replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    return Moment(iso=at.isoformat(), spoken=f"{hour} giờ")


async def _started(db, *, user_id=None, thread_id=None, asked_at=None, kind="typed"):
    ledger = TurnLedger(db)
    turn_id = str(uuid.uuid4())
    user_id = user_id or str(uuid.uuid4())
    thread_id = thread_id or str(uuid.uuid4())
    asked_at = asked_at or now_utc()
    await ledger.start(
        turn_id=turn_id, user_id=user_id, thread_id=thread_id, asked_at=asked_at, kind=kind
    )
    return ledger, turn_id, user_id, thread_id, asked_at


async def test_unannounced_returns_latest_unsaid_approval(test_db):
    ledger, turn_id, user_id, _, asked_at = await _started(test_db)
    outcome = PendingOutcome(
        outcome="approved", proposal_id="p1", kind="book", at=_moment()
    )
    await ledger.set_decision(turn_id, outcome)

    found = await ledger.unannounced(user_id, today=to_local(asked_at).date())

    assert found is not None
    assert found.turn_id == turn_id
    assert found.outcome == "approved"
    assert found.proposal_kind == "book"
    assert found.error is None


async def test_unannounced_ignores_clarify_and_picks_latest(test_db):
    user_id = str(uuid.uuid4())
    asked_at = now_utc()
    ledger, first_id, _, _, _ = await _started(
        test_db, user_id=user_id, asked_at=asked_at - timedelta(minutes=5)
    )
    await ledger.set_decision(
        first_id,
        PendingOutcome(outcome="clarify", proposal_id="p0", kind="book", at=_moment()),
    )
    _, second_id, _, _, _ = await _started(test_db, user_id=user_id, asked_at=asked_at)
    await ledger.set_decision(
        second_id,
        PendingOutcome(outcome="failed", proposal_id="p1", kind="move", at=_moment(), error="hết chỗ"),
    )

    found = await ledger.unannounced(user_id, today=to_local(asked_at).date())

    assert found is not None
    assert found.turn_id == second_id
    assert found.outcome == "failed"
    assert found.error == "hết chỗ"


async def test_mark_announced_clears_it(test_db):
    ledger, turn_id, user_id, _, asked_at = await _started(test_db)
    await ledger.set_decision(
        turn_id, PendingOutcome(outcome="approved", proposal_id="p1", kind="book", at=_moment())
    )

    await ledger.mark_announced(turn_id)

    found = await ledger.unannounced(user_id, today=to_local(asked_at).date())
    assert found is None


async def test_set_decision_inside_a_transaction_rolls_back_with_it(test_db):
    ledger, turn_id, user_id, _, asked_at = await _started(test_db)
    outcome = PendingOutcome(outcome="approved", proposal_id="p1", kind="book", at=_moment())

    class _Boom(Exception):
        pass

    async def write(session):
        await ledger.set_decision(turn_id, outcome, session=session)
        raise _Boom("force abort")

    async with await test_db.client.start_session() as session:
        with pytest.raises(_Boom):
            await session.with_transaction(write)

    record = await ledger.record(turn_id)
    assert record is not None
    assert record.decision is None
    assert await ledger.unannounced(user_id, today=to_local(asked_at).date()) is None


async def test_record_roundtrips_facts(test_db):
    ledger, turn_id, user_id, thread_id, asked_at = await _started(test_db, kind="button")
    time_fact = TimeFact(role="asked", moment=_moment(10))
    proposal_fact = ProposalFact(
        proposal_id="p1", proposal_kind="book", at=_moment(10), service_ids=["s1"]
    )
    await ledger.add_facts(turn_id, [time_fact, proposal_fact])
    await ledger.finish(turn_id, ended_by="answer", answer_source="llm")

    record = await ledger.record(turn_id)

    assert record is not None
    assert record.turn_id == turn_id
    assert record.user_id == user_id
    assert record.thread_id == thread_id
    assert record.day == to_local(asked_at).date()
    assert record.kind == "button"
    assert record.facts == [time_fact, proposal_fact]
    assert record.ended_by == "answer"
    assert record.answer_source == "llm"
    assert record.announced is False
    assert record.decision is None


async def test_record_returns_none_for_unknown_turn(test_db):
    ledger = TurnLedger(test_db)
    assert await ledger.record("not-a-real-turn") is None


async def test_service_facts_of_the_thread(test_db):
    thread_a = str(uuid.uuid4())
    thread_b = str(uuid.uuid4())
    base = now_utc()

    ledger, turn_1, _, _, _ = await _started(test_db, thread_id=thread_a, asked_at=base)
    old_hair = ServiceFact(
        service_id="hair", name="Cắt tóc", price_min=50000, price_max=50000, duration_minutes=30
    )
    await ledger.add_facts(turn_1, [old_hair])

    _, turn_2, _, _, _ = await _started(
        test_db, thread_id=thread_a, asked_at=base + timedelta(minutes=5)
    )
    new_hair = ServiceFact(
        service_id="hair", name="Cắt tóc", price_min=60000, price_max=60000, duration_minutes=30
    )
    nail = ServiceFact(
        service_id="nail", name="Sơn móng", price_min=80000, price_max=80000, duration_minutes=45
    )
    await ledger.add_facts(turn_2, [new_hair, nail])

    _, turn_other_thread, _, _, _ = await _started(test_db, thread_id=thread_b, asked_at=base)
    await ledger.add_facts(
        turn_other_thread,
        [ServiceFact(service_id="spa", name="Spa", price_min=1, price_max=1, duration_minutes=1)],
    )

    facts = await ledger.service_facts(thread_id=thread_a)

    by_id = {f.service_id: f for f in facts}
    assert set(by_id) == {"hair", "nail"}
    assert by_id["hair"].price_min == 60000  # bản sau (turn_2) đè bản trước
    assert "spa" not in by_id


async def test_collection_name_is_salon_turns(test_db):
    assert LEDGER_COLLECTION == "salon_turns"
