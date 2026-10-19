"""Cửa sổ lịch sử model — MỘT thuật toán (`select_window`), MỘT mốc cắt
(`session_cutoff`), hai nguồn: `conversations` (A) và message trong thread
checkpointer (B). Review Focus #3: hai nơi lệch nhau một mili-giây là tin đã nén
bị gửi lại, hoặc tin chưa nén bị bỏ.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.clock import TZ, now_ms, now_utc
from app.services.conversation import ConversationService, select_window, session_cutoff


def _pair(q, a, at):
    from app.agents.booking_graph.history import stamp
    return [stamp(HumanMessage(q), at), stamp(AIMessage(a), at + timedelta(milliseconds=1))]


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


# --- stamp ------------------------------------------------------------------

def test_stamp_round_trips_and_unstamped_is_none():
    from app.agents.booking_graph.history import stamp, stamped_at
    at = now_ms()
    assert stamped_at(stamp(HumanMessage("x"), at)) == at
    assert stamped_at(HumanMessage("x")) is None


# --- một thuật toán, hai nguồn ----------------------------------------------

async def _conversation_and_thread(test_db, items):
    """Ghi cùng một chuỗi (role, content, at) vào `conversations` và dựng các
    message thread tương ứng."""
    from app.agents.booking_graph.history import stamp
    svc = ConversationService(test_db)
    thread = []
    for role, content, at in items:
        await svc.append("u1", role, content, created_at=at)
        thread.append(stamp((HumanMessage if role == "user" else AIMessage)(content), at))
    return svc, thread


def _six_items(now):
    return [
        ("user" if i % 2 == 0 else "assistant", f"tin {i} " + "chữ " * (i * 30),
         now - timedelta(minutes=6 - i))
        for i in range(6)
    ]


@pytest.mark.parametrize("budget", [1500, 120, 60, 1])
async def test_select_window_matches_history_on_the_same_data(test_db, budget):
    from app.agents.booking_graph.history import model_history
    now = now_ms()
    svc, thread = await _conversation_and_thread(test_db, _six_items(now))

    via_history = [m.content for m in await svc.history("u1", token_budget=budget)]
    via_select = [m.content for m in select_window(
        await svc._all_messages("u1"), cutoff=svc.session_cutoff(), after=None,
        token_budget=budget)]
    via_thread = [m.content for m in model_history(thread, after=None, token_budget=budget)]
    assert via_history == via_select == via_thread


def test_model_history_drops_messages_up_to_covers_until():
    from app.agents.booking_graph.history import model_history, stamped_at
    t = now_ms() - timedelta(minutes=10)
    t1, t2, t3 = t, t + timedelta(minutes=1), t + timedelta(minutes=2)
    msgs = [*_pair("q1", "a1", t1), *_pair("q2", "a2", t2), *_pair("q3", "a3", t3)]
    kept = model_history(msgs, after=stamped_at(msgs[3]))   # covers_until = câu đáp a2
    assert [m.content for m in kept] == ["q3", "a3"]


async def test_same_millisecond_stamp_is_treated_as_compacted(test_db):
    """Thread và `conversations` mang CÙNG một `now_ms()`; covers_until đọc từ
    Mongo (mili-giây) phải loại đúng tin đó khỏi thread."""
    from app.agents.booking_graph.history import model_history, stamp
    svc = ConversationService(test_db)
    asked = now_ms()
    await asyncio.sleep(0.002)
    answered = now_ms()
    await svc.append("u1", "user", "q", created_at=asked)
    await svc.append("u1", "assistant", "a", created_at=answered)
    covers_until = (await svc._all_messages("u1"))[-1].created_at   # đọc lại từ Mongo

    thread = [stamp(HumanMessage("q"), asked), stamp(AIMessage("a"), answered)]
    assert model_history(thread, after=covers_until) == []


def test_microsecond_stamp_would_leak_compacted_message():
    """Lý do của now_ms: stamp micro-giây lớn hơn mốc mili-giây Mongo trả về,
    tin đã nén bị coi là 'sau mốc' và gửi lại."""
    from app.agents.booking_graph.history import model_history, stamp
    precise = now_utc().replace(microsecond=123456)
    from_mongo = precise.replace(microsecond=123000)
    assert model_history([stamp(AIMessage("a"), precise)], after=from_mongo) != []


def test_model_history_keeps_last_30_minutes_across_midnight():
    from app.agents.booking_graph.history import model_history, stamp
    now = datetime(2026, 10, 18, 0, 5, tzinfo=TZ).astimezone(timezone.utc)
    msgs = [
        stamp(HumanMessage("22h"), now - timedelta(hours=2, minutes=5)),
        stamp(AIMessage("đáp 22h"), now - timedelta(hours=2, minutes=4)),
        stamp(HumanMessage("23h50"), now - timedelta(minutes=15)),
        stamp(AIMessage("đáp 23h50"), now - timedelta(minutes=14)),
    ]
    assert [m.content for m in model_history(msgs, after=None, now=now)] == ["23h50", "đáp 23h50"]


def test_session_cutoff_is_day_start_or_30_minutes_back():
    noon = datetime(2026, 10, 18, 12, 0, tzinfo=TZ)
    assert session_cutoff(noon) == datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    after_midnight = datetime(2026, 10, 18, 0, 10, tzinfo=TZ)
    assert session_cutoff(after_midnight) == after_midnight - timedelta(minutes=30)


@pytest.mark.parametrize("source", ["conversations", "thread"])
async def test_budget_drops_a_leading_assistant_only_when_truncated(test_db, source):
    """F20: một test chạy qua cả hai bộ chuyển (thay cho 'bản sao' của test history())."""
    from app.agents.booking_graph.history import model_history
    now = now_ms()
    items = [("user", "câu hỏi rất dài " * 20, now - timedelta(minutes=3)),
             ("assistant", "câu đáp ngắn", now - timedelta(minutes=2)),
             ("user", "hỏi tiếp", now - timedelta(minutes=1)),
             ("assistant", "đáp tiếp", now - timedelta(seconds=30))]
    svc, thread = await _conversation_and_thread(test_db, items)

    async def window(budget):
        if source == "conversations":
            return [m.content for m in await svc.history("u1", token_budget=budget)]
        return [m.content for m in model_history(thread, after=None, token_budget=budget)]

    # Bị cắt mất câu hỏi → câu đáp mồ côi đứng đầu bị bỏ.
    assert await window(10) == ["hỏi tiếp", "đáp tiếp"]
    # Không cắt gì mà cửa sổ mở đầu bằng câu đáp (ca nửa đêm) → giữ nguyên.
    await test_db["conversations"].drop()
    svc, thread = await _conversation_and_thread(test_db, items[1:])
    assert await window(1500) == ["câu đáp ngắn", "hỏi tiếp", "đáp tiếp"]


def test_unstamped_messages_count_as_now_and_are_kept():
    from app.agents.booking_graph.history import model_history
    msgs = [HumanMessage("q"), AIMessage("a")]
    assert [m.content for m in model_history(msgs, after=now_utc() - timedelta(minutes=1))] == ["q", "a"]


def test_question_of_a_failed_turn_is_not_history():
    """A chỉ ghi `conversations` khi lượt có câu đáp. Trong thread, lượt lỗi
    (hoặc bị huỷ) để lại câu khách không có câu đáp — model không được thấy nó,
    giống A."""
    from app.agents.booking_graph.history import model_history, stamp
    t = now_ms() - timedelta(minutes=5)
    msgs = [*_pair("q1", "a1", t),
            stamp(HumanMessage("q lỗi"), t + timedelta(minutes=1)),
            *_pair("q2", "a2", t + timedelta(minutes=2)),
            stamp(HumanMessage("q lỗi nữa"), t + timedelta(minutes=3))]
    assert [m.content for m in model_history(msgs, after=None)] == ["q1", "a1", "q2", "a2"]


def test_tool_and_system_messages_are_never_history():
    from langchain_core.messages import ToolMessage

    from app.agents.booking_graph.history import model_history
    msgs = [HumanMessage("q"), AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "c"}]),
            ToolMessage("kq", tool_call_id="c"), AIMessage("a"), SystemMessage("s")]
    assert [m.content for m in model_history(msgs, after=None)] == ["q", "a"]


# --- middleware ---------------------------------------------------------------

async def test_middleware_sends_only_the_window_and_keeps_layout_order():
    from langchain.agents import create_agent

    from app.agents.booking_graph.middleware.history import HistoryWindowMiddleware
    from app.agents.booking_graph.middleware.layout import MessageLayoutMiddleware
    from tests.fakes import scripted
    t = now_ms() - timedelta(minutes=10)
    old, new = _pair("cũ", "đáp cũ", t), _pair("mới", "đáp mới", t + timedelta(minutes=2))
    model = scripted([AIMessage("Dạ.")])
    agent = create_agent(model, [], system_prompt="p",
                         middleware=[HistoryWindowMiddleware(), MessageLayoutMiddleware()],
                         checkpointer=False)
    await agent.ainvoke({"messages": [*old, *new, HumanMessage("q")], "context_block": "CTX",
                         "history_after": t + timedelta(minutes=1)})
    sent = [m.content for m in model.calls[0]]
    assert sent == ["p", "mới", "đáp mới", "CTX", "q"]


async def test_middleware_keeps_this_turns_tool_exchange():
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    from app.agents.booking_graph.middleware.history import HistoryWindowMiddleware
    from app.agents.booking_graph.middleware.layout import MessageLayoutMiddleware
    from tests.fakes import scripted, tool_call

    @tool
    async def look(day: str) -> str:
        """x"""
        return "kết quả"

    model = scripted([tool_call("look", {"day": "d"}), AIMessage("Dạ.")])
    agent = create_agent(model, [look], system_prompt="p",
                         middleware=[HistoryWindowMiddleware(), MessageLayoutMiddleware()],
                         checkpointer=False)
    await agent.ainvoke({"messages": [HumanMessage("q")], "context_block": "CTX"})
    second = model.calls[1]
    assert [type(m).__name__ for m in second] == [
        "SystemMessage", "HumanMessage", "HumanMessage", "AIMessage", "ToolMessage"]
    assert second[2].content == "q" and second[4].content == "kết quả"


# --- phrase / supervisor đọc qua model_history --------------------------------

async def test_phrase_sends_only_the_window(monkeypatch):
    from app.agents.booking_graph import phrase
    from tests.fakes import scripted
    model = scripted([AIMessage("Xong rồi ạ.")])
    monkeypatch.setattr(phrase, "build_chat_model", lambda **kw: model)
    t = now_ms() - timedelta(minutes=10)
    old, new = _pair("cũ", "đáp cũ", t), _pair("mới", "đáp mới", t + timedelta(minutes=2))
    await phrase.write_phrase({
        "messages": [*old, *new, HumanMessage("ừ")], "context_block": "CTX",
        "history_after": t + timedelta(minutes=1),
        "confirm_fact": {"kind": "booked", "when": "x"}, "fallback": "f"})
    contents = [m.content for m in model.calls[0][1:]]
    assert contents == ["mới", "đáp mới", "CTX", "ừ"]


async def test_supervisor_sees_the_last_turns_of_the_window_only(monkeypatch):
    from app.agents.booking_graph import supervisor
    from tests.fakes import scripted
    model = scripted([AIMessage("social")], tags=("supervisor",))
    monkeypatch.setattr(supervisor, "build_chat_model", lambda **kw: model)
    t = now_ms() - timedelta(minutes=10)
    old, new = _pair("cũ", "đáp cũ", t), _pair("mới", "đáp mới", t + timedelta(minutes=2))
    await supervisor.supervise({"messages": [*old, *new, HumanMessage("chào")],
                                "history_after": t + timedelta(minutes=1)})
    assert [m.content for m in model.calls[0][1:]] == ["mới", "đáp mới", "chào"]
