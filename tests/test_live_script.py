"""Pure-helper tests for `scripts/test_live_10_turns.py` scenario B (Task 9).

Never imports or calls `main` — that calls Azure and, without `--keep-db`,
wipes the dev DB. Only the DB-picking helpers are exercised here, against
the shared test DB (`test_db` fixture).
"""
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ, now_utc, to_local
from app.core.slots import slot_keys_for
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.waiting_session import WaitingSessionService
from scripts.test_live_10_turns import (
    PICK_OPTION,
    customer_clock_phrase,
    keep_db_turns,
    pick_session_option,
    pick_taken_starts,
)

def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    day = to_local(now_utc()).date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=hour, minute=minute)


def test_customer_clock_phrase():
    assert customer_clock_phrase(_tomorrow_at(16)) == "4 giờ chiều"


@pytest.mark.asyncio
async def test_pick_taken_starts_skips_the_customer_and_needs_two(test_db):
    customer_id = "customer-id"
    other_id = "other-id"

    # Only the customer's own appointment tomorrow: must be excluded, so the
    # overall result stays None (fewer than two OTHER appointments).
    await test_db["appointments"].insert_one({
        "user_id": customer_id, "start_at": _tomorrow_at(9), "status": "booked",
        "slot_keys": slot_keys_for(_tomorrow_at(9), 60),
    })
    assert await pick_taken_starts(test_db, exclude_user_id=customer_id) is None

    # A single other-customer appointment: still not enough (need two).
    await test_db["appointments"].insert_one({
        "user_id": other_id, "start_at": _tomorrow_at(15), "status": "booked",
        "slot_keys": slot_keys_for(_tomorrow_at(15), 60),
    })
    assert await pick_taken_starts(test_db, exclude_user_id=customer_id) is None

    # A second, earlier other-customer appointment: now two distinct starts,
    # returned earliest first.
    await test_db["appointments"].insert_one({
        "user_id": other_id, "start_at": _tomorrow_at(11), "status": "booked",
        "slot_keys": slot_keys_for(_tomorrow_at(11), 60),
    })
    result = await pick_taken_starts(test_db, exclude_user_id=customer_id)
    assert result == (_tomorrow_at(11), _tomorrow_at(15))


@pytest.mark.asyncio
async def test_pick_session_option_reads_the_open_session(test_db):
    user_id = "waiting-user"
    assert await pick_session_option(test_db, user_id=user_id) is None

    option_dt = _tomorrow_at(17)
    session = WaitingSession(
        user_id=user_id,
        agent="appointment",
        task="book",
        memory={},
        question=SessionQuestion(
            field="at",
            options=[SessionOption(iso=to_local(option_dt).isoformat(), spoken="5 giờ chiều")],
            text="Giờ nào vậy em?",
        ),
        asked_at=now_utc(),
        expires_at=now_utc() + timedelta(minutes=10),
        opened_turn_id="turn-1",
    )
    await WaitingSessionService(test_db).open(session)

    result = await pick_session_option(test_db, user_id=user_id)
    assert result == option_dt


def test_keep_db_turns_shape():
    first_taken = _tomorrow_at(9)
    second_taken = _tomorrow_at(16)
    turns = keep_db_turns(first_taken, second_taken)

    assert len(turns) == 10
    assert turns[4][2] is None  # turn 5: button press
    assert turns[3][2] == PICK_OPTION  # turn 4
    assert turns[7][2] == PICK_OPTION  # turn 8
    assert customer_clock_phrase(first_taken) in turns[1][2]  # turn 2
    assert customer_clock_phrase(second_taken) in turns[6][2]  # turn 7
