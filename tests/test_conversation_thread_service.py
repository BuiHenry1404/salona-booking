"""Vòng đời cuộc trò chuyện (spec §3.4) — một thread Orchestrator mỗi ngày VN,
carry-over 30 phút quanh nửa đêm (`CARRY_OVER_MINUTES`)."""
from datetime import datetime, timezone

import pytest

from app.core.clock import TZ
from app.services.conversation_thread import ConversationThreadService

pytestmark = pytest.mark.asyncio


def vn(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=TZ).astimezone(timezone.utc)


async def test_first_turn_opens_a_conversation(test_db):
    service = ConversationThreadService(test_db)
    thread, opened = await service.current("u1", vn(2026, 11, 9, 9, 0))

    assert opened is True
    assert thread.previous_id is None


async def test_same_vn_day_continues(test_db):
    service = ConversationThreadService(test_db)
    first, _ = await service.current("u1", vn(2026, 11, 9, 8, 0))
    second, opened = await service.current("u1", vn(2026, 11, 9, 21, 0))

    assert opened is False
    assert second.id == first.id


async def test_reply_just_after_midnight_continues(test_db):
    service = ConversationThreadService(test_db)
    first, _ = await service.current("u1", vn(2026, 11, 9, 23, 58))
    second, opened = await service.current("u1", vn(2026, 11, 10, 0, 10))

    assert opened is False
    assert second.id == first.id


async def test_next_morning_opens_a_new_conversation_linked_to_the_previous(test_db):
    service = ConversationThreadService(test_db)
    first, _ = await service.current("u1", vn(2026, 11, 9, 23, 58))
    second, opened = await service.current("u1", vn(2026, 11, 10, 8, 0))

    assert opened is True
    assert second.id != first.id
    assert second.previous_id == str(first.id)


async def test_day_is_vietnam_day_not_utc(test_db):
    """06:30 VN rồi 07:30 VN cùng một ngày VN, dù mốc 06:30 VN đã là 23:30
    ngày hôm trước theo UTC — ranh giới phải cắt theo ngày VN, không phải UTC."""
    service = ConversationThreadService(test_db)
    first, _ = await service.current("u1", vn(2026, 11, 9, 6, 30))
    second, opened = await service.current("u1", vn(2026, 11, 9, 7, 30))

    assert opened is False
    assert second.id == first.id
