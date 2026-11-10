from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import now_utc, to_local
from app.services.conversation import ConversationService

pytestmark = pytest.mark.asyncio


async def test_append_then_read_back_today(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "mai 3h chiều được không")
    await svc.append("u1", "assistant", "dạ được ạ")

    today = to_local(now_utc()).date()
    assert [(m.role, m.content) for m in await svc.messages_on("u1", today)] == [
        ("user", "mai 3h chiều được không"),
        ("assistant", "dạ được ạ"),
    ]


async def test_append_uses_the_given_created_at(test_db):
    svc = ConversationService(test_db)
    at = datetime(2026, 10, 17, 1, 2, 3, 456000, tzinfo=timezone.utc)
    await svc.append("u1", "user", "chào", created_at=at)
    await svc.append("u1", "assistant", "dạ")
    msgs = await svc._all_messages("u1")
    assert msgs[0].created_at == at
    assert msgs[1].created_at != at


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


async def test_new_conversation_documents_carry_no_pending_flag(test_db):
    """M3: cờ chờ xác nhận chuyển sang `proposals`; document mới không mang nó."""
    await ConversationService(test_db).append("u1", "user", "chào")
    doc = await test_db["conversations"].find_one({"user_id": "u1"})
    assert "pending_confirmation" not in doc and "state" not in doc


async def test_an_old_document_with_legacy_fields_still_shows(test_db):
    """Document cũ còn `pending_confirmation` / `state` (bản nén của gói agent
    cũ, xoá ở Task 16) vẫn hiện bình thường trên màn lịch sử."""
    await test_db["conversations"].insert_one({
        "user_id": "u1",
        "messages": [{"role": "user", "content": "chào", "created_at": now_utc()}],
        "pending_confirmation": {"start_at": "x"},
        "state": {"day": now_utc(), "covers_until": now_utc(), "summary": ["x"],
                  "slots": {"intent": "book"}, "queued": [], "failures": 0},
    })
    svc = ConversationService(test_db)
    today = to_local(now_utc()).date()
    assert [m.content for m in await svc.messages_on("u1", today)] == ["chào"]
    assert [d.preview for d in await svc.list_days("u1")] == ["chào"]


class TestMessageSource:
    async def test_append_defaults_to_llm_and_can_mark_code(self, test_db):
        svc = ConversationService(test_db)
        await svc.append("u1", "assistant", "câu LLM")
        await svc.append("u1", "assistant", "câu code", source="code")
        msgs = await svc._all_messages("u1")
        assert [m.source for m in msgs] == ["llm", "code"]

    async def test_old_documents_without_source_read_as_llm(self, test_db):
        await test_db["conversations"].insert_one({"user_id": "u9", "messages": [
            {"role": "assistant", "content": "cũ", "created_at": now_utc()}]})
        msgs = await ConversationService(test_db)._all_messages("u9")
        assert msgs[0].source == "llm"
