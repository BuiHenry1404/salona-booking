from datetime import timedelta

import pytest

from app.core.clock import now_utc, to_local
from app.services.conversation import ConversationService

pytestmark = pytest.mark.asyncio


async def test_append_then_read_history(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "mai 3h chiều được không")
    await svc.append("u1", "assistant", "dạ được ạ")

    history = await svc.history("u1")
    assert [(m.role, m.content) for m in history] == [
        ("user", "mai 3h chiều được không"),
        ("assistant", "dạ được ạ"),
    ]


async def test_history_is_isolated_per_user(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "của u1")
    assert await svc.history("u2") == []


async def test_history_is_trimmed_to_the_token_budget(test_db):
    svc = ConversationService(test_db)
    for i in range(200):
        await svc.append("u1", "user", "câu nói khá dài để tốn token " * 5)

    history = await svc.history("u1", token_budget=200)
    assert 0 < len(history) < 200


async def test_history_keeps_the_most_recent_messages(test_db):
    svc = ConversationService(test_db)
    for i in range(50):
        await svc.append("u1", "user", f"tin nhắn số {i} " * 10)

    history = await svc.history("u1", token_budget=200)
    assert "tin nhắn số 49" in history[-1].content


async def test_history_drops_messages_from_previous_days(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "chuyện của ba tuần trước")
    await svc.append("u1", "user", "hôm nay muốn đặt lịch")
    stale = now_utc() - timedelta(days=21)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"messages.0.created_at": stale}}
    )

    history = await svc.history("u1")
    assert [m.content for m in history] == ["hôm nay muốn đặt lịch"]


async def test_history_keeps_the_last_half_hour_across_midnight(test_db):
    """Khách nhắn 23:58, AI hỏi xác nhận, khách đáp "ừ" lúc 00:01 — câu "ừ"
    không được mất ngữ cảnh chỉ vì đồng hồ sang ngày."""
    svc = ConversationService(test_db)
    await svc.append("u1", "assistant", "3 giờ chiều Thứ Năm đúng không cô?")
    await svc.append("u1", "user", "ừ")
    just_before_midnight = now_utc() - timedelta(minutes=5)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"messages.0.created_at": just_before_midnight}}
    )

    history = await svc.history("u1")
    assert len(history) == 2


async def test_list_days_groups_by_vietnam_local_day(test_db):
    """Gom theo UTC là cắt vào 7 giờ sáng ở VN — một buổi làm việc bị xé đôi."""
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "tin lúc nửa đêm UTC")
    await svc.append("u1", "user", "tin ngay sau đó")

    days = await svc.list_days("u1")
    assert len(days) == 1


async def test_list_days_previews_the_first_thing_the_customer_said(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "assistant", "Dạ con nghe đây ạ")
    await svc.append("u1", "user", "mai 3 giờ chiều làm tóc được không con")

    [today] = await svc.list_days("u1")
    assert today.preview.startswith("mai 3 giờ chiều")


async def test_list_days_is_newest_first(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "chuyện hôm kia")
    await svc.append("u1", "user", "chuyện hôm nay")
    stale = now_utc() - timedelta(days=2)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"messages.0.created_at": stale}}
    )

    days = await svc.list_days("u1")
    assert [d.message_count for d in days] == [1, 1]
    assert days[0].day > days[1].day


async def test_messages_on_returns_only_that_day(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "chuyện hôm kia")
    await svc.append("u1", "user", "chuyện hôm nay")
    stale = now_utc() - timedelta(days=2)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"messages.0.created_at": stale}}
    )

    today = to_local(now_utc()).date()
    messages = await svc.messages_on("u1", today)
    assert [m.content for m in messages] == ["chuyện hôm nay"]


async def test_messages_on_a_day_are_isolated_per_user(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "của u1")
    today = to_local(now_utc()).date()
    assert await svc.messages_on("u2", today) == []


async def test_messages_on_a_day_is_not_trimmed_by_token_budget(test_db):
    """Màn lịch sử là để đọc lại, không phải để nhồi vào prompt — không cắt."""
    svc = ConversationService(test_db)
    for i in range(80):
        await svc.append("u1", "user", f"tin nhắn số {i} " * 10)

    today = to_local(now_utc()).date()
    assert len(await svc.messages_on("u1", today)) == 80


async def test_pending_confirmation_round_trip(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "2026-08-07T08:00:00+00:00", "note": "làm tóc"})
    pending = await svc.get_pending("u1")
    assert pending["note"] == "làm tóc"


async def test_pending_confirmation_expires(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "x", "note": "làm tóc"})
    stale = now_utc() - timedelta(minutes=30)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"pending_confirmation.asked_at": stale}}
    )
    assert await svc.get_pending("u1", max_age_minutes=10) is None


async def test_clearing_pending(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "x", "note": None})
    await svc.set_pending("u1", None)
    assert await svc.get_pending("u1") is None
