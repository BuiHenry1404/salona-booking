import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.clock import now_utc, to_local
from app.models.conversation import Digest
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


async def test_history_never_starts_with_an_orphaned_answer(test_db):
    """Cắt theo ngân sách đi từ tin mới nhất lùi về, nên chỗ cắt có thể rơi
    giữa một cặp và giữ lại câu trả lời mà bỏ mất câu hỏi sinh ra nó.

    Model đọc một câu đáp không có câu hỏi thì mất mạch — đó đúng là điều
    04-agent.md:99 cấm.

    Số học phải CHỐT CHẶT, không được để may rủi: giữ được số tin CHẴN thì
    tin cũ nhất tình cờ là `user` và test xanh cả khi chưa sửa gì. Mỗi tin
    dài đúng 30 ký tự -> cost = 30 // CHARS_PER_TOKEN = 10. Ngân sách 50 giữ
    đúng 5 tin — số LẺ — nên tin cũ nhất chắc chắn là `assistant`.
    """
    svc = ConversationService(test_db)
    for _ in range(10):
        await svc.append("u1", "user", "u" * 30)
        await svc.append("u1", "assistant", "a" * 30)

    history = await svc.history("u1", token_budget=50)

    assert history, "ngân sách 50 token phải đủ cho ít nhất một cặp"
    assert history[0].role == "user", [m.role for m in history]
    # Bỏ đúng một tin mồ côi, không bỏ cả cặp còn lành.
    assert len(history) == 4


async def test_history_keeps_whole_pairs_when_the_budget_is_tiny(test_db):
    """Ngân sách nhỏ tới mức chỉ đủ một tin: thà trả rỗng còn hơn trả một
    câu đáp mồ côi.

    Vòng lặp luôn giữ tin mới nhất dù vượt ngân sách (`and kept` chỉ chặn từ
    tin thứ hai), nên chưa sửa thì hàm trả về đúng một `assistant` mồ côi.
    """
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "u" * 30)
    await svc.append("u1", "assistant", "a" * 30)

    assert await svc.history("u1", token_budget=1) == []


async def test_a_truncated_window_of_three_still_drops_the_orphan(test_db):
    """Ca mà luật đếm-độ-dài bỏ lọt: cắt còn ĐÚNG BA tin, tin đầu là câu đáp
    mồ côi thật. Số học: mỗi tin 30 ký tự -> cost 10; budget 35 giữ 3 tin."""
    svc = ConversationService(test_db)
    for _ in range(3):
        await svc.append("u1", "user", "u" * 30)
        await svc.append("u1", "assistant", "a" * 30)

    history = await svc.history("u1", token_budget=35)

    assert [m.role for m in history] == ["user", "assistant"]


def _digest(covers_until, bullets=("Khách muốn làm tóc.",), day=None, failures=0):
    return Digest(day=day or to_local(now_utc()).date(), covers_until=covers_until,
                  bullets=list(bullets), failures=failures)


class TestDigestStorage:
    async def test_round_trip_for_today(self, test_db):
        svc = ConversationService(test_db)
        await svc.append("u1", "user", "x")
        await svc.set_digest("u1", _digest(now_utc()))
        got = await svc.get_digest("u1")
        assert got.bullets == ["Khách muốn làm tóc."]

    async def test_yesterdays_digest_is_invisible(self, test_db):
        svc = ConversationService(test_db)
        await svc.set_digest("u1", _digest(now_utc(), day=date(2000, 1, 1)))
        assert await svc.get_digest("u1") is None

    async def test_no_digest_is_none(self, test_db):
        assert await ConversationService(test_db).get_digest("u1") is None

    async def test_bump_failures_increments(self, test_db):
        svc = ConversationService(test_db)
        await svc.set_digest("u1", _digest(now_utc()))
        await svc.bump_digest_failures("u1")
        await svc.bump_digest_failures("u1")
        assert (await svc.get_digest("u1")).failures == 2

    async def test_bump_failures_without_digest_creates_an_empty_one(self, test_db):
        svc = ConversationService(test_db)
        await svc.bump_digest_failures("u1")
        got = await svc.get_digest("u1")
        assert got.failures == 1 and got.bullets == []
        assert got.covers_until == datetime(1970, 1, 1, tzinfo=timezone.utc)


class TestContextWindow:
    """Tin đã nén (<= covers_until) không đi nguyên văn nữa — chỗ tiết kiệm token."""

    async def _seed(self, svc, n):
        for i in range(n):
            await svc.append("u1", "user", f"hỏi {i}")
            await svc.append("u1", "assistant", f"đáp {i}")
            # Mongo lưu `created_at` với độ chính xác mili giây; append liên
            # tiếp trong vòng lặp có thể trùng mốc, làm bộ lọc `after` (so
            # sánh nghiêm ngặt) sai — chờ một chút để mỗi tin có mốc riêng.
            await asyncio.sleep(0.002)
        return await svc._all_messages("u1")

    async def test_without_digest_returns_everything_like_history(self, test_db):
        svc = ConversationService(test_db)
        await self._seed(svc, 3)
        bullets, msgs = await svc.context_window("u1")
        assert bullets == []
        assert [m.content for m in msgs] == [m.content for m in await svc.history("u1")]

    async def test_messages_covered_by_the_digest_are_dropped(self, test_db):
        svc = ConversationService(test_db)
        all_msgs = await self._seed(svc, 5)
        cut = all_msgs[3].created_at            # nén tới hết tin thứ 4
        await svc.set_digest("u1", _digest(cut, bullets=["Khách hỏi 0 và 1."]))

        bullets, msgs = await svc.context_window("u1")
        assert bullets == ["Khách hỏi 0 và 1."]
        assert [m.content for m in msgs] == [m.content for m in all_msgs[4:]]

    async def test_yesterdays_digest_does_not_cut_todays_messages(self, test_db):
        svc = ConversationService(test_db)
        all_msgs = await self._seed(svc, 2)
        await svc.set_digest("u1", _digest(all_msgs[-1].created_at, day=date(2000, 1, 1)))
        bullets, msgs = await svc.context_window("u1")
        assert bullets == [] and len(msgs) == 4

    async def test_history_after_filters_strictly_greater(self, test_db):
        svc = ConversationService(test_db)
        all_msgs = await self._seed(svc, 2)
        kept = await svc.history("u1", after=all_msgs[1].created_at)
        assert [m.content for m in kept] == [m.content for m in all_msgs[2:]]

    async def test_session_messages_untrimmed_unlike_history_with_tiny_budget(self, test_db):
        svc = ConversationService(test_db)
        all_msgs = await self._seed(svc, 5)

        session = await svc.session_messages("u1")
        assert [m.content for m in session] == [m.content for m in all_msgs]

        trimmed = await svc.history("u1", token_budget=1)
        assert len(trimmed) < len(all_msgs)
