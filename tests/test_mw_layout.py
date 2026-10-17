"""Bố cục message model nhận (CONTEXT.md bẫy #9): System → [summary + slots] →
lịch sử → khối bối cảnh → câu khách → tool exchange của lượt này."""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.booking_graph.middleware.layout import layout_messages
from app.models.conversation import ConversationSlots

CTX = "Bạn đang nói chuyện với: Cô Lan (0912345678)."


def contents(ms):
    return [m.content for m in ms]


def test_context_block_sits_right_before_the_question():
    out = layout_messages([HumanMessage("câu cũ"), AIMessage("đáp cũ"), HumanMessage("câu mới")],
                          context_block=CTX, summary=[], slots=None)
    assert contents(out) == ["câu cũ", "đáp cũ", CTX, "câu mới"]


def test_tool_exchange_of_this_turn_stays_after_the_question():
    tc = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "c1"}])
    out = layout_messages([HumanMessage("q"), tc, ToolMessage("r", tool_call_id="c1")],
                          context_block=CTX, summary=[], slots=None)
    assert contents(out)[:2] == [CTX, "q"] and out[2] is tc and out[3].content == "r"


def test_summary_and_slots_ride_in_one_message_before_history():
    out = layout_messages([HumanMessage("hôm qua"), HumanMessage("còn không em")], context_block=CTX,
                          summary=["Khách muốn làm tóc."], slots=ConversationSlots(service="làm móng bột"))
    assert isinstance(out[0], HumanMessage)
    assert out[0].content.startswith("Diễn biến phần trước của cuộc trò chuyện hôm nay:")
    assert "- Khách muốn làm tóc." in out[0].content and "- Dịch vụ: làm móng bột" in out[0].content
    assert contents(out)[1:] == ["hôm qua", CTX, "còn không em"]


def test_slots_alone_still_make_a_block():
    out = layout_messages([HumanMessage("q")], context_block=CTX, summary=[],
                          slots=ConversationSlots(service="làm móng bột"))
    assert "- Dịch vụ: làm móng bột" in out[0].content
    assert contents(out)[1:] == [CTX, "q"]


def test_no_state_block_when_nothing_to_say():
    out = layout_messages([HumanMessage("q")], context_block=CTX, summary=[], slots=None)
    assert contents(out) == [CTX, "q"]


def test_empty_messages_do_not_crash():
    assert contents(layout_messages([], context_block=CTX, summary=[], slots=None)) == [CTX]


async def test_middleware_keeps_the_system_message_first_and_sends_the_layout():
    from langchain.agents import create_agent

    from app.agents.booking_graph.middleware.layout import MessageLayoutMiddleware
    from tests.fakes import scripted
    model = scripted([AIMessage("Dạ.")])
    agent = create_agent(model, [], system_prompt="prompt", middleware=[MessageLayoutMiddleware()])
    await agent.ainvoke({"messages": [HumanMessage("q")], "context_block": CTX})
    assert contents(model.calls[0]) == ["prompt", CTX, "q"]


async def test_hang_reply_sleeps_then_answers():
    """Hạ tầng cho Task 2.4: model giả treo được (huỷ được giữa chừng)."""
    from tests.fakes import Hang, scripted
    model = scripted([Hang(3600)])
    task = asyncio.ensure_future(model.ainvoke([HumanMessage("q")]))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()

    quick = scripted([Hang(0.01, AIMessage("Dạ."))])
    assert (await quick.ainvoke([HumanMessage("q")])).content == "Dạ."
