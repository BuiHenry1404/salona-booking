"""Task 5.4: `app/agents/booking/events.py` — `run_turn` trên graph mới,
không `interrupt()` (§5.3, §6, §9.1, §10, §11 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Mongo thật (`test_db`), model giả (`ScriptedChatModel`, `tests/fakes.py`) cắm
vào 4 chỗ dựng model của graph mới — như `tests/test_graph_turn.py`. Test ở
đây chỉ kiểm phần `events.py` THÊM vào graph (thread theo ngày, một lượt một
khách, `conversations` song song với thread, `complete.proposal`, đánh dấu
`announced`, `TurnFacts`, trần thời gian, nhật ký không chữ khách) — hành vi
riêng của graph đã có test ở tầng của nó (`test_graph_turn.py`).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from langchain_core.messages import AIMessage

from app.agents.booking import events
from app.agents.booking import graph as graph_module
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.schemas import AppointmentRefOut, Moment, PendingOutcome
from app.agents.booking.button import ButtonReply
from app.agents.booking.context import address_phrase
from app.core.clock import TZ, now_ms, now_utc, to_local
from app.core.config import settings
from app.core.vi_format import format_vi_datetime
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation import ConversationService
from app.services.conversation_state import ConversationStateService
from app.services.proposal import ProposalService
from tests.fakes import FakeSpecialists, Hang, scripted, tool_call

ORCH_MODEL = "app.agents.booking.orchestrator.agent.build_chat_model"
CONV_MODEL = "app.agents.booking.conversation.build_chat_model"
SPECIALIST_MODEL = "app.agents.booking.specialists.base.build_chat_model"
REWRITE_MODEL = "app.agents.booking.rewrite.build_chat_model"


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


@pytest.fixture
async def user(test_db):
    phone = f"09{uuid.uuid4().int % 100000000:08d}"
    return await AuthService(test_db).create_user(phone, "matkhau123", "Nguyễn Thị Lan")


@pytest.fixture
def models(monkeypatch):
    """Model giả cho 4 chỗ dựng model của graph mới. Nén nền tắt (gọi LLM thật)."""
    bag = SimpleNamespace(
        orchestrator=scripted([], tags=("orchestrate",)),
        conversation=scripted([], tags=("respond",)),
        specialist=scripted([], tags=("appointment",)),
        rewrite=scripted([], tags=("rewrite",)),
        specialists=FakeSpecialists({}),
    )
    monkeypatch.setattr(ORCH_MODEL, lambda **kw: bag.orchestrator)
    monkeypatch.setattr(CONV_MODEL, lambda **kw: bag.conversation)
    monkeypatch.setattr(SPECIALIST_MODEL, lambda **kw: bag.specialist)
    monkeypatch.setattr(REWRITE_MODEL, lambda **kw: bag.rewrite)

    async def fake_appointment(db, user, spec, ctx, question, *, extra_middleware=()):
        return await bag.specialists.appointment(spec, ctx, question)

    async def fake_shop(db, user, spec, ctx, question, *, extra_middleware=()):
        return await bag.specialists.shop(spec, ctx, question)

    monkeypatch.setattr(graph_module.appointment, "run_task", fake_appointment)
    monkeypatch.setattr(graph_module.shop_info, "run_task", fake_shop)
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    monkeypatch.setattr(events, "get_callbacks", lambda: [])
    return bag


def _finish(kind_hint="social") -> AIMessage:
    return tool_call("finish", {"kind_hint": kind_hint, "service": None, "stated": []})


async def turn(test_db, user, text):
    return [(e.type, e.data) async for e in events.run_turn(test_db, user, text)]


def contents(batch):
    return [m.content for m in batch]


async def thread_values(test_db, user, thread_id=None):
    thread_id = thread_id or events.thread_id_for(str(user.id), now_ms())
    graph = graph_module.build_graph(test_db, user)
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return snap.values


# ---------------------------------------------------------------------------
# `translate_langchain_event` — chỉ token tag "respond" được chuyển tiếp.
# ---------------------------------------------------------------------------


def _lc_stream_event(tags, text="Dạ"):
    class _Chunk:
        content = text

    return {"event": "on_chat_model_stream", "tags": list(tags), "data": {"chunk": _Chunk()}}


@pytest.mark.parametrize("tag", ["orchestrate", "appointment", "shop_info", "rewrite"])
def test_translate_langchain_event_drops_tokens_from_every_tag_but_respond(tag):
    assert events.translate_langchain_event(_lc_stream_event([tag])) == []


def test_translate_langchain_event_forwards_the_respond_tagged_token():
    result = events.translate_langchain_event(_lc_stream_event(["respond"]))
    assert [(e.type, e.data["text"]) for e in result] == [("token", "Dạ")]


# ---------------------------------------------------------------------------
# Thread checkpointer: lịch sử model đến từ thread, không từ `conversations`.
# ---------------------------------------------------------------------------


async def test_second_turn_sees_the_first_turn_from_the_thread(test_db, user, models):
    models.orchestrator.replies += [_finish(), _finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ."),
                                    AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]
    assert (await turn(test_db, user, "chào em"))[-1][0] == "complete"
    # Xoá nhật ký hiển thị: lượt 2 vẫn phải thấy lượt 1 — lịch sử model đến từ thread.
    await test_db["conversations"].update_one({}, {"$set": {"messages": []}})

    result = await turn(test_db, user, "cảm ơn em")
    assert result[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé.", "proposal": None})
    batch = str(contents(models.conversation.calls[-1]))
    assert "chào em" in batch and "Dạ em chào chị Lan ạ." in batch and "cảm ơn em" in batch


async def test_thread_holds_only_question_and_answer_not_tool_messages(test_db, user, models):
    models.specialists = FakeSpecialists({})
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ tiệm mở cửa cả tuần ạ.")]
    await turn(test_db, user, "tiệm mở mấy giờ")

    messages = (await thread_values(test_db, user))["messages"]
    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage"]
    assert not messages[1].tool_calls
    assert contents(messages) == ["tiệm mở mấy giờ", "Dạ tiệm mở cửa cả tuần ạ."]


async def test_conversations_and_thread_share_timestamps(test_db, user, models):
    from app.agents.booking.history import stamped_at
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    await turn(test_db, user, "chào em")

    thread = (await thread_values(test_db, user))["messages"]
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert [m.created_at for m in stored] == [stamped_at(m) for m in thread]
    assert stored[0].created_at < stored[1].created_at


async def test_compacted_messages_are_not_resent_to_the_conversation_model(test_db, user, models):
    from app.models.conversation import ConversationState
    models.orchestrator.replies += [_finish(), _finish(), _finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ."),
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
    batch = str(contents(models.conversation.calls[-1]))
    assert "chào em" not in batch and "Dạ em chào chị Lan ạ." not in batch
    assert "chị muốn làm tóc" in batch and "Dạ chị Lan muốn làm gì ạ?" in batch


# ---------------------------------------------------------------------------
# `complete.proposal`
# ---------------------------------------------------------------------------


async def test_complete_proposal_is_set_whenever_a_pending_proposal_exists(test_db, user, models):
    """Đề xuất tạo ở lượt TRƯỚC (`ProposalService.create` trực tiếp, như một
    lượt khác vừa `appointment_task(propose_book)`) — lượt này chỉ "ừ để hỏi
    con" (decide_pending none → kept): đề xuất vẫn pending, `complete.proposal`
    phải khác `None`."""
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    models.orchestrator.replies += [tool_call("decide_pending", {
        "label": "none", "aside": "defer_with_request", "reason": "để hỏi con"})]
    models.conversation.replies += [AIMessage("Dạ vâng chị Lan.")]

    result = await turn(test_db, user, "để chị hỏi con đã nha em",
                        )
    _type, data = result[-1]
    assert data["proposal"] is not None
    assert data["proposal"]["id"] == str(proposal.id)
    assert data["proposal"]["kind"] == "book"
    assert data["proposal"]["when_spoken"] == format_vi_datetime(start)


@pytest.mark.parametrize("decision", ["yes", "no"])
async def test_complete_proposal_is_none_after_approve_or_reject(test_db, user, models, decision):
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    reply = "Dạ em đặt xong rồi nhé." if decision == "yes" else "Dạ vâng chị Lan."
    models.conversation.replies += [AIMessage(reply)]
    button = ButtonReply(decision=decision, proposal_id=str(proposal.id), label="Đồng ý")

    result = await turn(test_db, user, button)
    _type, data = result[-1]
    assert data["proposal"] is None
    assert models.orchestrator.calls == []          # R1: nút không chạy Orchestrator


# ---------------------------------------------------------------------------
# Nút bấm khi không còn gì để duyệt — vẫn chạy graph (route "button"), không
# rẽ nhánh riêng ở `events.py`; chỉ không chạm DB nghiệp vụ nào.
# ---------------------------------------------------------------------------


async def test_button_turn_when_nothing_is_pending_makes_no_model_call_and_writes_no_appointment(
    test_db, user, models
):
    from app.agents.booking.reply_brief import BUTTON_STALE_REPLY
    button = ButtonReply(decision="yes", proposal_id="khong-con-ton-tai", label="Đồng ý")

    result = await turn(test_db, user, button)

    expected = BUTTON_STALE_REPLY.replace("anh chị", address_phrase(user))
    assert [t for t, _ in result] == ["turn_started", "complete"]
    assert result[-1] == ("complete", {"answer": expected, "proposal": None})
    assert models.conversation.calls == [] and models.orchestrator.calls == []

    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["Đồng ý", expected]
    appointments = await test_db["appointments"].count_documents({})
    assert appointments == 0


# ---------------------------------------------------------------------------
# Một thread một lượt chạy — `_supersede`/`_ACTIVE_TURNS`.
# ---------------------------------------------------------------------------


async def _wait_until(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "timeout"
        await asyncio.sleep(0.01)


async def _wait_until_async(predicate, timeout=5.0):
    """Như `_wait_until`, nhưng `predicate` là coroutine (đọc DB) — chờ một
    ĐIỀU KIỆN thật, không phải một khoảng `sleep` cố định đoán là đủ."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await predicate():
        assert loop.time() < deadline, "timeout"
        await asyncio.sleep(0.01)


async def test_a_new_turn_supersedes_a_hung_turn(test_db, user, models):
    models.orchestrator.replies += [Hang(3600), _finish()]
    models.conversation.replies += [AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]

    first = asyncio.create_task(turn(test_db, user, "alo em"))
    await _wait_until(lambda: len(models.orchestrator.calls) == 1)

    second = await asyncio.wait_for(turn(test_db, user, "em ơi"), timeout=5)
    assert second[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé.", "proposal": None})
    await asyncio.wait({first}, timeout=1)
    assert first.cancelled()

    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["em ơi", "Dạ chị Lan cần gì cứ nhắn em nhé."]
    assert str(user.id) not in events._ACTIVE_TURNS


async def test_turns_of_two_users_do_not_cancel_each_other(test_db, user, models):
    other = await AuthService(test_db).create_user("0987654321", "matkhau123", "Chú Tư")
    models.orchestrator.replies += [Hang(3600), _finish()]
    models.conversation.replies += [AIMessage("Dạ anh Tư cần gì cứ nhắn em nhé.")]

    hung = asyncio.create_task(turn(test_db, user, "alo em"))
    await _wait_until(lambda: len(models.orchestrator.calls) == 1)
    try:
        done = await asyncio.wait_for(turn(test_db, other, "em ơi"), timeout=5)
        assert done[-1][0] == "complete"
        assert not hung.done()
    finally:
        hung.cancel()
        await asyncio.wait({hung}, timeout=1)
    assert events._ACTIVE_TURNS == {}


async def test_three_quick_turns_only_the_newest_runs(test_db, user, models, monkeypatch):
    logged = []
    monkeypatch.setattr(events.logger, "info", lambda msg, **kw: logged.append(msg))
    models.orchestrator.replies += [Hang(3600), _finish()]
    models.conversation.replies += [AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]

    t1 = asyncio.create_task(turn(test_db, user, "q1"))
    await _wait_until(lambda: len(models.orchestrator.calls) == 1)
    t2 = asyncio.create_task(turn(test_db, user, "q2"))
    await asyncio.sleep(0)
    t3 = asyncio.create_task(turn(test_db, user, "q3"))

    done = await asyncio.wait_for(t3, timeout=5)
    await asyncio.wait({t1, t2}, timeout=1)
    assert done[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé.", "proposal": None})
    assert t1.cancelled() and t2.cancelled()
    assert logged.count("turn_superseded") == 2
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["q3", "Dạ chị Lan cần gì cứ nhắn em nhé."]
    assert events._ACTIVE_TURNS == {}


async def test_cancel_after_record_still_stores_the_whole_pair(test_db, user, models, monkeypatch):
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    real_append = ConversationService.append
    holder = {}

    async def slow_append(self, user_id, role, *a, **kw):
        await real_append(self, user_id, role, *a, **kw)
        if role == "user":
            holder["task"].cancel()
            await asyncio.sleep(0.05)

    monkeypatch.setattr(ConversationService, "append", slow_append)
    holder["task"] = asyncio.create_task(turn(test_db, user, "chào em"))
    await asyncio.wait({holder["task"]}, timeout=2)
    await asyncio.sleep(0.2)
    stored = await ConversationService(test_db)._all_messages(str(user.id))
    assert contents(stored) == ["chào em", "Dạ em chào chị Lan ạ."]


async def test_writes_after_complete_survive_a_cancel_right_after_complete_is_yielded(
    test_db, user, models
):
    """Fix round 1 (#1): khách gõ tin kế tiếp NGAY sau khi nhận `complete` —
    `_supersede` của lượt mới huỷ task đang emit `complete`. `mark_announced`
    (lượt này VÀ `announced_turn_id` của R0) cùng `TurnFacts` phải chạy
    `shield` nên vẫn ghi xong, dù task tiêu thụ generator đã bị `cancel()`."""
    ledger = TurnLedger(test_db)
    asked_at = now_ms()
    start = _tomorrow_at(15)
    spoken = format_vi_datetime(start)
    await ledger.start(turn_id="prev-turn", user_id=str(user.id), route="orchestrate",
                       rule="R2", asked_at=asked_at - timedelta(seconds=1))
    pending = PendingOutcome(
        outcome="approved", proposal_id="p-prev", kind="book", spoken=spoken,
        appointment=AppointmentRefOut(id="a-prev", at=Moment(iso=start.isoformat(), spoken=spoken)),
    )
    await ledger.set_pending_outcome("prev-turn", pending)

    models.orchestrator.replies += [tool_call(
        "finish", {"kind_hint": "answer", "service": "móng gel", "stated": ["muốn làm móng gel"]})]
    models.conversation.replies += [
        AIMessage(f"Dạ chị Lan, em đã đặt xong {spoken} rồi ạ.")]

    seen = []

    async def consume():
        me = asyncio.current_task()
        async for event in events.run_turn(test_db, user, "alo em"):
            seen.append((event.type, event.data))
            if event.type == "complete":
                me.cancel()          # huỷ NGAY sau khi khách đã nhận complete

    task = asyncio.create_task(consume())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen[-1][0] == "complete"
    assert str(user.id) not in events._ACTIVE_TURNS

    async def _writes_landed():
        prev_doc = await ledger.record_of("prev-turn")
        docs = [d async for d in test_db["turn_ledger"].find({"user_id": str(user.id)})]
        this_doc = next((d for d in docs if d["turn_id"] != "prev-turn"), None)
        state = await ConversationStateService(test_db).conversations.get_state(str(user.id))
        return bool(
            prev_doc and prev_doc["announced"]
            and this_doc and this_doc["announced"]
            and state and state.slots and state.slots.service == "móng gel"
        )

    await _wait_until_async(_writes_landed)


# ---------------------------------------------------------------------------
# Langfuse session_id = user_id, không phải thread_id.
# ---------------------------------------------------------------------------


async def test_langfuse_session_is_the_user_not_the_thread(test_db, user, monkeypatch):
    seen = {}

    class FakeGraph:
        async def astream_events(self, state, config, version):
            seen.update(config)
            seen["state"] = state
            return
            yield  # pragma: no cover - faz deste um gerador

        async def aget_state(self, config):
            return SimpleNamespace(values={"answer": "Dạ.", "messages": []})

    monkeypatch.setattr(events, "build_graph", lambda db, u: FakeGraph())
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    result = await turn(test_db, user, "chào em")
    assert result[-1] == ("complete", {"answer": "Dạ.", "proposal": None})
    assert seen["metadata"]["langfuse_session_id"] == str(user.id)
    assert seen["metadata"]["langfuse_user_id"] == str(user.id)
    assert seen["configurable"]["thread_id"].startswith(f"{user.id}:")
    # Mọi khoá của lượt phải có mặt — không bỏ khoá nào (thread giữ giá trị cũ).
    for key in ("turn_id", "asked_at", "customer_text", "button",
               "previous_replies", "history_after", "messages"):
        assert key in seen["state"], key
    assert seen["state"]["button"] is None


# ---------------------------------------------------------------------------
# Trần thời gian một lượt.
# ---------------------------------------------------------------------------


async def test_turn_timeout_emits_the_same_error(test_db, user, models, monkeypatch):
    monkeypatch.setattr(settings, "orch_turn_timeout_seconds", 0)
    models.orchestrator.replies += [Hang(5)]

    result = await turn(test_db, user, "chào em")
    assert [t for t, _ in result] == ["turn_started", "error"]
    assert result[-1] == ("error", {"message": events.TURN_ERROR_MESSAGE})


def test_turn_timeout_seconds_reads_settings_at_call_time(monkeypatch):
    monkeypatch.setattr(settings, "orch_turn_timeout_seconds", 42)
    assert events.turn_timeout_seconds() == 42


# ---------------------------------------------------------------------------
# Đánh dấu `announced` sau `complete`.
# ---------------------------------------------------------------------------


async def test_announced_is_set_after_complete(test_db, user, models):
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ.")]

    result = await turn(test_db, user, "chào em")
    assert result[-1][0] == "complete"

    doc = await test_db["turn_ledger"].find_one({"user_id": str(user.id)})
    assert doc is not None and doc["announced"] is True


async def test_announced_turn_id_also_gets_marked_when_r0_reports_it(test_db, user, models):
    """R0: lượt TRƯỚC có kết quả chưa báo; lượt này báo bù trong câu đáp ->
    cả hai lượt (lượt này VÀ lượt trước) phải `announced=True`."""
    ledger = TurnLedger(test_db)
    asked_at = now_ms()
    start = _tomorrow_at(15)
    spoken = format_vi_datetime(start)
    await ledger.start(turn_id="prev-turn", user_id=str(user.id), route="orchestrate",
                       rule="R2", asked_at=asked_at - timedelta(seconds=1))
    pending = PendingOutcome(
        outcome="approved", proposal_id="p-prev", kind="book", spoken=spoken,
        appointment=AppointmentRefOut(id="a-prev", at=Moment(iso=start.isoformat(), spoken=spoken)),
    )
    await ledger.set_pending_outcome("prev-turn", pending)

    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [
        AIMessage(f"Dạ chị Lan, em đã đặt xong {spoken} rồi ạ.")]

    result = await turn(test_db, user, "alo em")
    assert result[-1][0] == "complete"

    prev_doc = await ledger.record_of("prev-turn")
    assert prev_doc["announced"] is True
    docs = [d async for d in test_db["turn_ledger"].find({"user_id": str(user.id)})]
    this_doc = next(d for d in docs if d["turn_id"] != "prev-turn")
    assert this_doc["announced"] is True

    # Lượt 3: cả hai lượt trước đã `announced=True` -> resolver không còn gì
    # để coi là "chưa báo" nữa; câu đáp lượt 3 không cần (và không) lặp lại
    # dòng R0 của lượt 1.
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ chị Lan cần gì nữa không ạ?")]
    with structlog.testing.capture_logs() as logs:
        third = await turn(test_db, user, "cảm ơn em")

    assert third[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì nữa không ạ?", "proposal": None})
    resolve_entry = next(e for e in logs if e["event"] == "resolve_done")
    assert resolve_entry["extra"]["has_unannounced"] is False
    assert spoken not in third[-1][1]["answer"]


# ---------------------------------------------------------------------------
# `TurnFacts` → slots, ngay sau `complete`.
# ---------------------------------------------------------------------------


async def test_apply_turn_facts_runs_after_complete(test_db, user, models):
    models.orchestrator.replies += [tool_call(
        "finish", {"kind_hint": "answer", "service": "móng gel", "stated": ["muốn làm móng gel"]})]
    models.conversation.replies += [AIMessage("Dạ chị muốn làm ngày nào ạ?")]

    await turn(test_db, user, "chị muốn làm móng gel")

    state = await ConversationStateService(test_db).conversations.get_state(str(user.id))
    assert state.slots.stated == ["muốn làm móng gel"]
    assert state.slots.service == "móng gel"


# ---------------------------------------------------------------------------
# §9.1: input ≤ 1000 ký tự, một dòng.
# ---------------------------------------------------------------------------


async def test_customer_text_is_capped_at_1000_chars_and_single_line(test_db, user, models):
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ vâng ạ.")]
    raw = ("chào em\n" * 300) + "x" * 900

    await turn(test_db, user, raw)

    messages = (await thread_values(test_db, user))["messages"]
    stored_question = messages[0].content
    assert "\n" not in stored_question
    assert len(stored_question) <= 1000


# ---------------------------------------------------------------------------
# Câu cuối LUÔN là `state["answer"]` (ghi chú controller bắt buộc) — không
# bao giờ là chữ ghép từ các token đã stream.
# ---------------------------------------------------------------------------


async def test_complete_answer_is_the_final_guarded_state_not_the_streamed_tokens(
    test_db, user, models, monkeypatch
):
    models.orchestrator.replies += [_finish()]
    # Model trả một câu, nhưng guard (patch) trả về một câu CUỐI khác hẳn —
    # mô phỏng guard viết lại nguyên câu (vd. rewrite/fallback).
    models.conversation.replies += [AIMessage("câu nháp bị guard thay")]

    async def fake_guard(draft, reply, previous_replies, *, source, log_extra=None):
        from app.agents.booking.guard import GuardResult
        return GuardResult(answer="câu CUỐI sau guard", source="code", violations=["x"])

    import app.agents.booking.graph as graph_mod
    monkeypatch.setattr(graph_mod, "apply_guard", fake_guard)

    result = await turn(test_db, user, "chào em")

    assert result[-1] == ("complete", {"answer": "câu CUỐI sau guard", "proposal": None})
    # `complete.answer` KHÔNG BAO GIỜ là chữ ghép từ các token đã stream —
    # stream mang bản NHÁP (trước guard), `complete` mang câu CUỐI (sau guard).
    tokens = "".join(d.get("text", "") for t, d in result if t == "token")
    assert tokens == "câu nháp bị guard thay"
    assert tokens != "câu CUỐI sau guard"


# ---------------------------------------------------------------------------
# §10: `conversations` ghi hỏng sau khi `record` đã ghi thread.
# ---------------------------------------------------------------------------


async def test_persist_failure_marks_the_ledger_persist_failed_and_still_completes(
    test_db, user, models, monkeypatch
):
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ.")]

    async def broken_append(self, *a, **kw):
        raise RuntimeError("mongo hong")

    monkeypatch.setattr(ConversationService, "append", broken_append)

    result = await turn(test_db, user, "chào em")
    assert result[-1] == (
        "complete", {"answer": "Dạ em chào chị Lan ạ.", "proposal": None})

    docs = [d async for d in test_db["turn_ledger"].find({"user_id": str(user.id)})]
    assert len(docs) == 1 and docs[0]["ended_by"] == "persist_failed"


# ---------------------------------------------------------------------------
# AST: không `aupdate_state`/`Command(resume=...)` ở `events.py`/`graph.py`.
# ---------------------------------------------------------------------------


def _calls_forbidden_resume_api(path) -> list[str]:
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "aupdate_state":
                hits.append("aupdate_state")
            if isinstance(node.func, ast.Name) and node.func.id == "Command":
                for kw in node.keywords:
                    if kw.arg == "resume":
                        hits.append("Command(resume=...)")
    return hits


def test_no_code_path_calls_aupdate_state_or_command_resume():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking"
    hits = {
        str(p.relative_to(root)): _calls_forbidden_resume_api(p)
        for p in (root / "events.py", root / "graph.py")
    }
    assert all(not v for v in hits.values()), hits


# ---------------------------------------------------------------------------
# §11: log không chữ khách, có `user_id`/`turn_id`/`thread_id`.
# ---------------------------------------------------------------------------


async def test_turn_events_do_not_log_the_customer_text(test_db, user, models):
    models.orchestrator.replies += [_finish()]
    models.conversation.replies += [AIMessage("Dạ em chào chị Lan ạ.")]

    with structlog.testing.capture_logs() as logs:
        await turn(test_db, user, "một câu rất riêng tư của khách")

    assert not any("một câu rất riêng tư của khách" in str(entry) for entry in logs)


# ---------------------------------------------------------------------------
# Review Focus #2: kết quả chưa báo (R0) sống qua một lượt bị `_supersede`.
# ---------------------------------------------------------------------------


async def test_unannounced_outcome_is_said_next_turn(test_db, user, models):
    """Lượt 1: `decide_pending(approve)` ghi lịch THẬT ngay trong `orchestrate`
    (`apply_decision` ghi `pending_outcome` vào `turn_ledger` NGAY, không chờ
    `record` — `app/agents/booking/orchestrator/decide.py`), rồi TREO ở
    Conversation (`respond`). Lượt 2 "alo em" tới SUPERSEDE lượt 1 trước khi
    nó kịp phát `complete`. Lượt 2 phải tự báo bù (R0, `đã đặt xong {spoken}`)
    trong câu đáp; SAU lượt 2 cả hai ledger doc (lượt 1 VÀ lượt 2) phải
    `announced=True` — hành vi ĐÚNG hiện tại của fix Task 5.3/5.4 (khác ghi
    chú "ledger lượt 1 vẫn False" của brief, vốn mô tả hành vi TRƯỚC fix đó).
    Lượt 3 không còn gì chưa báo -> không lặp lại dòng R0."""
    start = _tomorrow_at(15)
    spoken = format_vi_datetime(start)
    await ProposalService(test_db).create(user, "book", start, None)

    models.orchestrator.replies += [
        tool_call("decide_pending", {"label": "approve", "aside": None, "reason": "dong y"}),
        _finish("answer"),
    ]
    models.conversation.replies += [Hang(3600)]

    first = asyncio.create_task(turn(test_db, user, "ừ"))
    await _wait_until(lambda: len(models.conversation.calls) == 1)

    # Lịch đã ghi THẬT dù lượt 1 chưa phát `complete` (còn treo ở Conversation).
    appointments = await test_db["appointments"].count_documents({"user_id": str(user.id)})
    assert appointments == 1

    models.orchestrator.replies += [_finish("answer")]
    models.conversation.replies += [AIMessage(f"Dạ chị Lan, em đã đặt xong {spoken} rồi ạ.")]

    second = await asyncio.wait_for(turn(test_db, user, "alo em"), timeout=5)
    await asyncio.wait({first}, timeout=1)
    assert first.cancelled()

    assert second[-1][0] == "complete"
    answer2 = dict(second)["complete"]["answer"]
    assert spoken in answer2

    docs = [d async for d in test_db["turn_ledger"].find({"user_id": str(user.id)})]
    assert len(docs) == 2
    assert all(d["announced"] is True for d in docs)

    # Lượt 3: cả hai lượt trước đã `announced=True` -> không còn gì chưa báo,
    # câu đáp không lặp lại dòng R0.
    models.orchestrator.replies += [_finish("answer")]
    models.conversation.replies += [AIMessage("Dạ chị Lan cần gì nữa không ạ?")]
    with structlog.testing.capture_logs() as logs:
        third = await turn(test_db, user, "cảm ơn em")

    assert third[-1] == (
        "complete", {"answer": "Dạ chị Lan cần gì nữa không ạ?", "proposal": None})
    assert spoken not in third[-1][1]["answer"]
    resolve_entry = next(e for e in logs if e["event"] == "resolve_done")
    assert resolve_entry["extra"]["has_unannounced"] is False


# ---------------------------------------------------------------------------
# Review Focus #5: an toàn khi khởi động lại (checkpoint mất) và nút cũ.
# ---------------------------------------------------------------------------


async def test_restart_does_not_resume_and_stale_button_is_safe(test_db, user, models):
    """Đề xuất đang chờ sống trong `proposals` (Mongo), KHÔNG trong thread
    checkpointer — "khởi động lại" (checkpoint mất, vd. process restart) vẫn
    duyệt được bình thường: lắp một `InMemorySaver` MỚI thay cho cái fixture
    `memory_checkpointer` (conftest) đã cài, mô phỏng mất mọi thread state.
    `build_graph` không có tham số mặc định đóng băng — nó đọc checkpointer
    hiện hành LÚC DỰNG (`get_checkpointer()`), nên swap này có hiệu lực ngay
    từ lượt tiếp theo mà không cần build lại gì khác.

    Sau khi lượt "ừ" chạy xong (từ START, không `aupdate_state`/resume), đề
    xuất GỐC đã `approved` -> một NÚT CŨ gửi lên với đúng id đề xuất đó (máy
    khách vẫn còn hiện nút sau restart) phải an toàn: `ctx.pending` giờ là
    `None` nên route "button" tự trả `stale_button`, không gọi Orchestrator,
    không ghi thêm gì."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents import checkpoint
    from app.agents.booking.reply_brief import BUTTON_STALE_REPLY

    start = _tomorrow_at(15)
    spoken = format_vi_datetime(start)
    old_proposal = await ProposalService(test_db).create(user, "book", start, None)

    checkpoint.set_checkpointer(InMemorySaver())  # "restart": thread state mất

    models.orchestrator.replies += [
        tool_call("decide_pending", {"label": "approve", "aside": None, "reason": "dong y"}),
        _finish(),
    ]
    models.conversation.replies += [AIMessage(f"Dạ em đặt xong {spoken} cho chị rồi nhé.")]

    result = await turn(test_db, user, "ừ")

    assert result[-1][0] == "complete"
    reloaded = await ProposalService(test_db).get(user, str(old_proposal.id))
    assert reloaded.status == "approved"
    appointments = await AppointmentService(test_db).upcoming_for(user)
    assert len(appointments) == 1
    messages = (await thread_values(test_db, user))["messages"]
    # Chạy hẳn từ START trên checkpointer mới: đúng MỘT cặp hỏi-đáp, không có
    # gì "resume" được từ một thread cũ không tồn tại.
    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage"]

    orch_calls_before = len(models.orchestrator.calls)
    appointments_before = len(await AppointmentService(test_db).upcoming_for(user))
    button = ButtonReply(decision="yes", proposal_id=str(old_proposal.id), label="Đồng ý")

    result2 = await turn(test_db, user, button)

    expected = BUTTON_STALE_REPLY.replace("anh chị", address_phrase(user))
    assert result2[-1] == ("complete", {"answer": expected, "proposal": None})
    assert len(models.orchestrator.calls) == orch_calls_before      # không chạy Orchestrator
    appointments_after = len(await AppointmentService(test_db).upcoming_for(user))
    assert appointments_after == appointments_before                # không ghi gì thêm


# ---------------------------------------------------------------------------
# §7.3: trần thời gian một lượt phải còn dư cho Conversation và cho vòng
# orchestrate (2 task song song + 1 lượt model) sau khi `CLASSIFY_TIMEOUT_SECONDS`
# bị bỏ (Task 4.4b, 06/11 — Orchestrator tự `finish(kind_hint=...)`).
# ---------------------------------------------------------------------------


def test_turn_time_budget_inequality():
    from app.agents.booking.conversation import RESPOND_TIMEOUT_SECONDS
    from app.agents.llm import LLM_TIMEOUT_SECONDS

    assert (
        settings.orch_orchestrate_timeout_seconds + RESPOND_TIMEOUT_SECONDS + 5
        <= settings.orch_turn_timeout_seconds
    )
    assert (
        settings.orch_task_timeout_seconds * 2 + LLM_TIMEOUT_SECONDS
        <= settings.orch_orchestrate_timeout_seconds
    )
