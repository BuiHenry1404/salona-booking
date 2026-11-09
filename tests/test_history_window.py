"""Cửa sổ lịch sử `conversations` — MỘT thuật toán (`select_window`), MỘT mốc
cắt (`session_cutoff`), và mốc mili-giây `now_ms()` dùng chung với message
trong thread checkpointer. Phần so với thread của gói agent cũ (`model_history`,
`stamp`) đã xoá cùng gói ở Task 16.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import TZ, now_ms, now_utc
from app.services.conversation import ConversationService, select_window, session_cutoff


# --- now_ms / append(created_at) -------------------------------------------

def test_now_ms_is_truncated_to_the_millisecond():
    t = now_ms()
    assert t.tzinfo is not None and t.microsecond % 1000 == 0
    assert abs((now_utc() - t).total_seconds()) < 1


async def test_append_uses_the_given_created_at(test_db):
    svc = ConversationService(test_db)
    at = datetime(2026, 10, 17, 1, 2, 3, 456000, tzinfo=timezone.utc)
    await svc.append("u1", "user", "chào", created_at=at)
    await svc.append("u1", "assistant", "dạ")
    msgs = await svc._all_messages("u1")
    assert msgs[0].created_at == at
    assert msgs[1].created_at != at


# --- một thuật toán ----------------------------------------------------------

async def _conversation(test_db, items):
    """Ghi chuỗi (role, content, at) vào `conversations`."""
    svc = ConversationService(test_db)
    for role, content, at in items:
        await svc.append("u1", role, content, created_at=at)
    return svc


def _six_items(now):
    return [
        ("user" if i % 2 == 0 else "assistant", f"tin {i} " + "chữ " * (i * 30),
         now - timedelta(minutes=6 - i))
        for i in range(6)
    ]


@pytest.mark.parametrize("budget", [1500, 120, 60, 1])
async def test_select_window_matches_history_on_the_same_data(test_db, budget):
    now = now_ms()
    svc = await _conversation(test_db, _six_items(now))

    via_history = [m.content for m in await svc.history("u1", token_budget=budget)]
    via_select = [m.content for m in select_window(
        await svc._all_messages("u1"), cutoff=svc.session_cutoff(), after=None,
        token_budget=budget)]
    assert via_history == via_select


def test_session_cutoff_is_day_start_or_30_minutes_back():
    noon = datetime(2026, 10, 18, 12, 0, tzinfo=TZ)
    assert session_cutoff(noon) == datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    after_midnight = datetime(2026, 10, 18, 0, 10, tzinfo=TZ)
    assert session_cutoff(after_midnight) == after_midnight - timedelta(minutes=30)


async def test_budget_drops_a_leading_assistant_only_when_truncated(test_db):
    now = now_ms()
    items = [("user", "câu hỏi rất dài " * 20, now - timedelta(minutes=3)),
             ("assistant", "câu đáp ngắn", now - timedelta(minutes=2)),
             ("user", "hỏi tiếp", now - timedelta(minutes=1)),
             ("assistant", "đáp tiếp", now - timedelta(seconds=30))]
    svc = await _conversation(test_db, items)

    async def window(budget):
        return [m.content for m in await svc.history("u1", token_budget=budget)]

    # Bị cắt mất câu hỏi → câu đáp mồ côi đứng đầu bị bỏ.
    assert await window(10) == ["hỏi tiếp", "đáp tiếp"]
    # Không cắt gì mà cửa sổ mở đầu bằng câu đáp (ca nửa đêm) → giữ nguyên.
    await test_db["conversations"].drop()
    svc = await _conversation(test_db, items[1:])
    assert await window(1500) == ["câu đáp ngắn", "hỏi tiếp", "đáp tiếp"]
