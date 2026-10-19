"""`run_turn` trên thread checkpointer `<user>:<ngày VN>` (M2).

Graph thật, model giả (`tests/fakes.py`), checkpointer `InMemorySaver` (fixture
autouse `memory_checkpointer` ở conftest). Khẳng định chính: model thấy ĐÚNG
những gì A cho nó thấy, chỉ khác nguồn — lịch sử lấy từ thread, `conversations`
vẫn là nhật ký hiển thị.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph import agents, events, rewrite, supervisor
from app.core.clock import TZ, now_ms
from app.services.auth import AuthService
from app.services.conversation import ConversationService
from tests.fakes import Hang, scripted, tool_call


@pytest.fixture
async def user(test_db):
    return await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")


@pytest.fixture
def models(monkeypatch):
    """Một model giả cho supervisor, một cho cả ba agent (chỉ agent được định
    tuyến chạy). Nén nền tắt — nó gọi LLM thật."""
    sup = scripted([], tags=("supervisor",))
    agent = scripted([])
    monkeypatch.setattr(supervisor, "build_chat_model", lambda **kw: sup)
    monkeypatch.setattr(agents, "build_chat_model", lambda **kw: agent)
    monkeypatch.setattr(rewrite, "build_chat_model", lambda **kw: scripted([AIMessage("")]))
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    monkeypatch.setattr(events, "get_callbacks", lambda: [])   # không đẩy trace Langfuse
    return SimpleNamespace(sup=sup, agent=agent)


def routes(*names):
    return [AIMessage(n) for n in names]


async def turn(test_db, user, text):
    return [(e.type, e.data) async for e in events.run_turn(test_db, user, text)]


def thread_config(user, now=None):
    return {"configurable": {"thread_id": events.thread_id_for(str(user.id), now or now_ms())}}


async def thread_values(test_db, user):
    from app.agents.booking_graph.graph import build_graph
    return (await build_graph(test_db, user).aget_state(thread_config(user))).values


def contents(batch):
    return [m.content for m in batch]


async def test_second_turn_sees_the_first_turn_from_the_thread(test_db, user, models):
    models.sup.replies += routes("social", "social")
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ."),
                             AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]
    assert (await turn(test_db, user, "chào em"))[-1][0] == "complete"
    # Xoá nhật ký: lượt 2 vẫn phải thấy lượt 1 — lịch sử model đến từ thread.
    await test_db["conversations"].update_one({}, {"$set": {"messages": []}})

    assert (await turn(test_db, user, "cảm ơn em"))[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé."})
    batch = contents(models.agent.calls[-1])
    assert batch[1:3] == ["chào em", "Dạ em chào chị Lan ạ."]
    assert batch[-1] == "cảm ơn em"
    assert batch[-2].startswith("Bây giờ là")          # khối bối cảnh sát câu khách (#6, #9)


async def test_thread_holds_only_question_and_answer_not_tool_messages(test_db, user, models):
    models.sup.replies += routes("booking")
    models.agent.replies += [tool_call("get_shop_hours", {}), AIMessage("Dạ tiệm mở cửa cả tuần ạ.")]
    await turn(test_db, user, "tiệm mở mấy giờ")

    messages = (await thread_values(test_db, user))["messages"]
    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage"]
    assert not messages[1].tool_calls
    assert contents(messages) == ["tiệm mở mấy giờ", "Dạ tiệm mở cửa cả tuần ạ."]


async def test_answer_of_previous_turn_never_leaks_into_this_turn(test_db, user, models):
    """A-07: lượt 2 lỗi → `error`, không phát lại câu đáp lượt 1 (vẫn nằm trong
    checkpoint), không ghi `conversations`. Lượt 3 chạy lại từ đầu và model không
    thấy câu hỏi mồ côi của lượt lỗi (A không bao giờ ghi nó)."""
    models.sup.replies += routes("social", "social", "social")
    # F13: model lỗi MỌI lần gọi của lượt 2, để retry (M4) không nuốt mất lỗi.
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ."),
                             *[RuntimeError("azure down")] * 3]
    await turn(test_db, user, "chào em")

    second = await turn(test_db, user, "đặt lịch")
    assert [t for t, _ in second] == ["turn_started", "error"]
    assert "Dạ em chào chị Lan ạ." not in str(second)
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["chào em", "Dạ em chào chị Lan ạ."]

    models.agent.replies[:] = [AIMessage("Dạ chị Lan nhắn lại giúp em nhé.")]
    third = await turn(test_db, user, "alo")
    assert third[-1] == ("complete", {"answer": "Dạ chị Lan nhắn lại giúp em nhé."})
    batch = contents(models.agent.calls[-1])
    assert "đặt lịch" not in batch
    assert batch[1:3] == ["chào em", "Dạ em chào chị Lan ạ."]


async def test_conversations_and_thread_share_timestamps(test_db, user, models):
    from app.agents.booking_graph.history import stamped_at
    models.sup.replies += routes("social")
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    await turn(test_db, user, "chào em")

    thread = (await thread_values(test_db, user))["messages"]
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert [m.created_at for m in stored] == [stamped_at(m) for m in thread]
    assert stored[0].created_at < stored[1].created_at


async def test_compacted_messages_are_not_resent_to_the_booking_model(test_db, user, models):
    """F2: `history_after` (= covers_until) phải tới được agent con."""
    from app.core.clock import now_utc, to_local
    from app.models.conversation import ConversationState
    models.sup.replies += routes("social", "social", "booking")
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ."),
                             AIMessage("Dạ chị Lan muốn làm gì ạ?"),
                             AIMessage("Dạ chị Lan chọn giờ nào ạ?")]
    await turn(test_db, user, "chào em")
    await turn(test_db, user, "chị muốn làm tóc")
    convs = ConversationService(test_db)
    stored = await convs._all_messages(str(user.id))
    await convs.set_state(str(user.id), ConversationState(
        day=to_local(now_utc()).date(), covers_until=stored[1].created_at,
        summary=["Khách chào."]))

    await turn(test_db, user, "mai được không")
    batch = contents(models.agent.calls[-1])
    assert "chào em" not in batch and "Dạ em chào chị Lan ạ." not in batch
    assert batch[1].endswith("- Khách chào.")
    assert batch[2:4] == ["chị muốn làm tóc", "Dạ chị Lan muốn làm gì ạ?"]
    # Supervisor cũng chỉ thấy phần chưa nén.
    assert "chào em" not in contents(models.sup.calls[-1])


def test_fresh_turn_fields_reset_everything_a_turn_produces():
    fields = events.fresh_turn_fields(
        {"context_block": "CTX", "summary": ["s"], "slots": None, "history_after": None,
         "history": [], "user_id": "u1"}, "q")
    # Lượt resume dùng chung hàm này: đề xuất đang chờ KHÔNG được đặt lại ở đây.
    assert "proposal_id" not in fields and "pending_confirmation" not in fields
    for key, empty in {"answer": "", "draft": "", "original_draft": "", "violations": [],
                       "rewritten": False, "route": "", "gate_next": "", "fallback": None,
                       "confirm_fact": None, "phrase_fact": None}.items():
        assert fields[key] == empty, key
    assert fields["customer_text"] == "q" and fields["user_id"] == "u1"
    assert "messages" not in fields


async def test_langfuse_session_is_the_user_not_the_thread(test_db, user, monkeypatch):
    seen = {}

    class FakeGraph:
        async def astream_events(self, state, config, version):
            seen.update(config)
            yield {"event": "on_chain_end", "data": {"output": {}}}

        async def aget_state(self, config):
            return SimpleNamespace(values={"answer": "Dạ.", "messages": []}, interrupts=(),
                                   created_at=None)

    monkeypatch.setattr(events, "build_graph", lambda db, u: FakeGraph())
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    await turn(test_db, user, "chào em")
    assert seen["metadata"]["langfuse_session_id"] == str(user.id)
    assert seen["metadata"]["langfuse_user_id"] == str(user.id)
    assert seen["configurable"]["thread_id"].startswith(f"{user.id}:")


def test_thread_id_changes_with_the_vn_day():
    # 16:59 UTC = 23:59 giờ VN; 17:01 UTC = 00:01 hôm sau.
    before = datetime(2026, 10, 17, 16, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 17, 17, 1, tzinfo=timezone.utc)
    assert events.thread_id_for("u1", before) == "u1:2026-10-17"
    assert events.thread_id_for("u1", after) == "u1:2026-10-18"
    # Nửa đêm UTC (7 giờ sáng VN) không đổi thread.
    assert events.thread_id_for("u1", datetime(2026, 10, 17, 23, 59, tzinfo=timezone.utc)) == \
        events.thread_id_for("u1", datetime(2026, 10, 18, 0, 1, tzinfo=timezone.utc))


async def _checkpoint_at(saver, thread_id, at):
    """Một checkpoint có `ts` cho trước — `StateSnapshot.created_at` đọc từ đó."""
    from langgraph.checkpoint.base import empty_checkpoint
    checkpoint = empty_checkpoint()
    checkpoint["ts"] = at.isoformat()
    await saver.aput({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                     checkpoint, {"source": "loop", "step": 1, "parents": {}}, {})


@pytest.mark.parametrize("last_at, expected_day", [
    (datetime(2026, 10, 17, 23, 55, tzinfo=TZ), "2026-10-17"),   # 15 phút trước → nối
    (datetime(2026, 10, 17, 23, 0, tzinfo=TZ), "2026-10-18"),    # 70 phút trước → thread mới
])
async def test_pick_thread_continues_yesterday_within_30_minutes_after_midnight(
        test_db, user, memory_checkpointer, last_at, expected_day):
    from app.agents.booking_graph.graph import build_graph
    uid = str(user.id)
    now = datetime(2026, 10, 18, 0, 10, tzinfo=TZ)
    await _checkpoint_at(memory_checkpointer, f"{uid}:2026-10-17", last_at)
    assert await events.pick_thread(build_graph(test_db, user), uid, now) == f"{uid}:{expected_day}"


async def test_pick_thread_prefers_todays_thread_once_it_exists(test_db, user, memory_checkpointer):
    from app.agents.booking_graph.graph import build_graph
    uid = str(user.id)
    now = datetime(2026, 10, 18, 0, 10, tzinfo=TZ)
    await _checkpoint_at(memory_checkpointer, f"{uid}:2026-10-17", now - timedelta(minutes=2))
    await _checkpoint_at(memory_checkpointer, f"{uid}:2026-10-18", now - timedelta(minutes=1))
    assert await events.pick_thread(build_graph(test_db, user), uid, now) == f"{uid}:2026-10-18"


async def test_pick_thread_stays_on_yesterdays_thread_while_its_messages_are_in_the_window(
        test_db, user, memory_checkpointer):
    """Khách nối qua nửa đêm lúc 00:10 rồi nhắn tiếp lúc 00:40: A vẫn giữ tin
    00:10 (sau đầu ngày). Đổi sang thread hôm nay ở đây là mất nó."""
    from app.agents.booking_graph.graph import build_graph
    uid = str(user.id)
    now = datetime(2026, 10, 18, 0, 40, tzinfo=TZ)
    await _checkpoint_at(memory_checkpointer, f"{uid}:2026-10-17", now - timedelta(minutes=30))
    assert await events.pick_thread(build_graph(test_db, user), uid, now) == f"{uid}:2026-10-17"


def test_build_graph_uses_the_installed_checkpointer_not_a_default_argument(memory_checkpointer):
    """F4: không mở MongoClient lúc import/định nghĩa; lấy checkpointer lúc dựng."""
    import inspect
    from unittest.mock import MagicMock

    from app.agents.booking_graph.graph import build_graph
    from app.models.user import User
    assert inspect.signature(build_graph).parameters["checkpointer"].default is None
    graph = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))
    assert graph.checkpointer is memory_checkpointer


async def test_two_turns_on_the_real_mongo_saver(test_db, user, models, monkeypatch):
    """State thật (message có stamp, pending có datetime, slots) qua serializer
    của MongoDBSaver — trong DB test, không phải checkpointing_db."""
    from pymongo import MongoClient

    from app.agents import checkpoint
    from app.core.config import settings
    from tests.conftest import TEST_DB_NAME, TEST_MONGO_URI
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    client = MongoClient(TEST_MONGO_URI, tz_aware=True)
    checkpoint.set_checkpointer(checkpoint.make_checkpointer(client))
    try:
        models.sup.replies += routes("social", "social")
        models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ."),
                                 AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]
        await turn(test_db, user, "chào em")
        assert (await turn(test_db, user, "cảm ơn em"))[-1][0] == "complete"
        assert contents(models.agent.calls[-1])[1:3] == ["chào em", "Dạ em chào chị Lan ạ."]
        assert await test_db["checkpoints"].count_documents(
            {"thread_id": events.thread_id_for(str(user.id), now_ms())}) > 0
    finally:
        client.close()


# --- Task 2.4: một thread một lượt chạy -------------------------------------

async def _wait_until(predicate, timeout=5.0):
    import asyncio
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "timeout"
        await asyncio.sleep(0.01)


async def test_a_new_turn_supersedes_a_hung_turn(test_db, user, models):
    """Azure treo ở lượt 1, khách nhắn lại: lượt 2 phải chạy (không kẹt sau
    lượt 1), lượt 1 bị huỷ êm — không `error`, không ghi `conversations`."""
    import asyncio

    from tests.fakes import Hang
    models.sup.replies += routes("social", "social")
    models.agent.replies += [Hang(3600), AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]

    first = asyncio.create_task(turn(test_db, user, "alo em"))
    await _wait_until(lambda: len(models.agent.calls) == 1)          # lượt 1 đang treo

    second = await asyncio.wait_for(turn(test_db, user, "em ơi"), timeout=5)
    assert second[-1] == ("complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé."})
    await asyncio.wait({first}, timeout=1)
    assert first.cancelled()

    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["em ơi", "Dạ chị Lan cần gì cứ nhắn em nhé."]
    assert "alo em" not in contents(models.agent.calls[-1])          # câu mồ côi không vào lịch sử
    assert str(user.id) not in events._ACTIVE_TURNS


async def test_turns_of_two_users_do_not_cancel_each_other(test_db, user, models):
    import asyncio

    from tests.fakes import Hang
    other = await AuthService(test_db).create_user("0987654321", "matkhau123", "Chú Tư")
    models.sup.replies += routes("social", "social")
    models.agent.replies += [Hang(3600), AIMessage("Dạ anh Tư cần gì cứ nhắn em nhé.")]

    hung = asyncio.create_task(turn(test_db, user, "alo em"))
    await _wait_until(lambda: len(models.agent.calls) == 1)
    try:
        done = await asyncio.wait_for(turn(test_db, other, "em ơi"), timeout=5)
        assert done[-1][0] == "complete"
        assert not hung.done()
    finally:
        hung.cancel()
        await asyncio.wait({hung}, timeout=1)
    assert events._ACTIVE_TURNS == {}


async def test_three_quick_turns_only_the_newest_runs_and_the_old_ones_log(
        test_db, user, models, monkeypatch):
    """Lượt 3 tới khi lượt 1 còn đang dừng: lượt 3 phải huỷ lượt 2 (đã đăng ký),
    không huỷ lại lượt 1 rồi chạy song song với lượt 2."""
    import asyncio
    logged = []
    monkeypatch.setattr(events.logger, "info", lambda msg, **kw: logged.append(msg))
    models.sup.replies += routes("social", "social", "social")
    # Lượt 2 bị huỷ ngay khi còn chờ lượt 1 dừng — không bao giờ tới model.
    models.agent.replies += [Hang(3600), AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]

    t1 = asyncio.create_task(turn(test_db, user, "q1"))
    await _wait_until(lambda: len(models.agent.calls) == 1)
    t2 = asyncio.create_task(turn(test_db, user, "q2"))
    await asyncio.sleep(0)                       # t2 đăng ký và đang chờ t1 dừng
    t3 = asyncio.create_task(turn(test_db, user, "q3"))

    done = await asyncio.wait_for(t3, timeout=5)
    await asyncio.wait({t1, t2}, timeout=1)
    assert done[-1] == ("complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé."})
    assert t1.cancelled() and t2.cancelled()
    assert logged.count("turn_superseded") == 2
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["q3", "Dạ chị Lan cần gì cứ nhắn em nhé."]
    assert len(models.agent.calls) == 2                       # chỉ lượt 1 và lượt 3
    assert events._ACTIVE_TURNS == {}


async def test_cancel_after_record_still_stores_the_whole_pair(test_db, user, models, monkeypatch):
    """`record` đã ghi câu đáp vào thread; bị huỷ giữa hai lần ghi nhật ký thì
    `conversations` vẫn phải có cả cặp, không có câu khách lẻ."""
    import asyncio
    models.sup.replies += routes("social")
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    real_append = ConversationService.append
    holder = {}

    async def slow_append(self, user_id, role, *a, **kw):
        await real_append(self, user_id, role, *a, **kw)
        if role == "user":
            holder["task"].cancel()              # lượt mới thay đúng lúc này
            await asyncio.sleep(0.05)

    monkeypatch.setattr(ConversationService, "append", slow_append)
    holder["task"] = asyncio.create_task(turn(test_db, user, "chào em"))
    await asyncio.wait({holder["task"]}, timeout=2)
    await asyncio.sleep(0.2)
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["chào em", "Dạ em chào chị Lan ạ."]


async def test_window_now_is_the_turn_time_not_each_model_call():
    """Cửa sổ lịch sử tính theo mốc của lượt (`asked_at`), như A tính một lần
    lúc nạp bối cảnh — không theo đồng hồ ở từng lần gọi model."""
    from app.agents.booking_graph.agents import build_agent, make_agent_node
    from app.agents.booking_graph.history import stamp
    from app.agents.booking_graph.middleware.history import HistoryWindowMiddleware
    asked = datetime(2030, 1, 2, 0, 5, tzinfo=TZ)
    msgs = [stamp(HumanMessage("22h"), datetime(2030, 1, 1, 22, 0, tzinfo=TZ)),
            stamp(AIMessage("đáp 22h"), datetime(2030, 1, 1, 22, 1, tzinfo=TZ)),
            stamp(HumanMessage("23h50"), datetime(2030, 1, 1, 23, 50, tzinfo=TZ)),
            stamp(AIMessage("đáp 23h50"), datetime(2030, 1, 1, 23, 51, tzinfo=TZ)),
            HumanMessage("q")]
    model = scripted([AIMessage("Dạ.")])
    node = make_agent_node(build_agent("p", [], model=model,
                                       extra_middleware=[HistoryWindowMiddleware()]))
    await node({"messages": msgs, "context_block": "CTX", "asked_at": asked})
    assert contents(model.calls[0]) == ["p", "23h50", "đáp 23h50", "CTX", "q"]
