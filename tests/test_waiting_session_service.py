"""Phiên chờ của agent con (spec §5) — mỗi khách tối đa một phiên cho mỗi
agent, hết hạn kiểm lúc đọc (`active`), sống qua lượt/khởi động lại vì nằm
trong Mongo chứ không trong state của graph."""
from datetime import datetime, timedelta
from typing import Optional

import pytest

from app.core.clock import now_utc
from app.models.waiting_session import SessionQuestion, WaitingSession
from app.services.waiting_session import WaitingSessionService

pytestmark = pytest.mark.asyncio


def a_session(
    user_id: str, agent: str, *, now: datetime, turn_id: str = "t1",
    task: str = "book", expires_at: Optional[datetime] = None,
) -> WaitingSession:
    return WaitingSession(
        user_id=user_id,
        agent=agent,
        task=task,
        memory={},
        question=SessionQuestion(field="day", text="Anh chị muốn đặt ngày nào ạ?"),
        asked_at=now,
        expires_at=expires_at if expires_at is not None else now + timedelta(minutes=10),
        opened_turn_id=turn_id,
    )


async def test_open_replaces_the_waiting_session_of_the_same_agent(test_db):
    service = WaitingSessionService(test_db)
    now = now_utc()
    await service.open(a_session("u1", "appointment", now=now, turn_id="t1", task="book"))
    await service.open(a_session("u1", "appointment", now=now, turn_id="t2", task="move"))

    active = await service.active("u1", now=now)
    assert active["appointment"].task == "move"
    assert await test_db["waiting_sessions"].count_documents({"user_id": "u1"}) == 1


async def test_sessions_of_two_agents_coexist(test_db):
    service = WaitingSessionService(test_db)
    now = now_utc()
    await service.open(a_session("u1", "appointment", now=now))
    await service.open(a_session("u1", "shop_info", now=now))

    active = await service.active("u1", now=now)
    assert set(active) == {"appointment", "shop_info"}


async def test_active_skips_expired_sessions(test_db):
    service = WaitingSessionService(test_db)
    now = now_utc()
    expired = a_session("u1", "appointment", now=now, expires_at=now - timedelta(seconds=1))
    await service.open(expired)

    active = await service.active("u1", now=now)
    assert "appointment" not in active


async def test_close_opened_in_turn_only_closes_that_turn(test_db):
    service = WaitingSessionService(test_db)
    now = now_utc()
    await service.open(a_session("u1", "appointment", now=now, turn_id="t1"))
    await service.open(a_session("u1", "shop_info", now=now, turn_id="t2"))

    closed = await service.close_opened_in_turn("u1", "t2", reason="code_reply")

    assert closed == ["shop_info"]
    active = await service.active("u1", now=now)
    assert set(active) == {"appointment"}


async def test_session_survives_a_new_service_instance(test_db):
    now = now_utc()
    await WaitingSessionService(test_db).open(a_session("u1", "appointment", now=now))

    active = await WaitingSessionService(test_db).active("u1", now=now)
    assert "appointment" in active


async def test_sessions_are_isolated_per_customer(test_db):
    service = WaitingSessionService(test_db)
    now = now_utc()
    await service.open(a_session("u1", "appointment", now=now))

    active = await service.active("u2", now=now)
    assert active == {}
