"""Giới hạn 4 vòng tool, lỗi tool thành câu tiếng Việt, tắt parallel tool calls.

Bẫy #23 (tool trả dữ kiện; câu lỗi giữ nguyên chữ) và Review Focus #5 (agent
không tool mà gửi `parallel_tool_calls` thì Azure trả 400).
"""
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.agents.booking_graph.agents import FALLBACK_ANSWER
from app.agents.booking_graph.middleware.tools import (TOOL_ERROR_TEXT,
                                                       NoParallelToolsMiddleware,
                                                       ToolErrorMiddleware,
                                                       ToolRoundLimitMiddleware)
from tests.fakes import scripted, tool_call


@tool
async def fake_lookup(day: str) -> str:
    """Tra cứu giả lập."""
    return f"còn trống lúc 3 giờ chiều ngày {day}"


@tool
async def broken_lookup(day: str) -> str:
    """Tra cứu hỏng."""
    raise RuntimeError("mongo down")


def all_mw():
    return [NoParallelToolsMiddleware(), ToolRoundLimitMiddleware(), ToolErrorMiddleware()]


def agent(model, tools=(fake_lookup,), middleware=None):
    return create_agent(model, list(tools), system_prompt="p",
                        middleware=all_mw() if middleware is None else middleware)


def ask(text="mai còn trống không em"):
    return {"messages": [HumanMessage(text)]}


async def test_four_model_calls_then_fallback():
    model = scripted([tool_call("fake_lookup", {"day": "2026-08-07"}, id=f"c{i}") for i in range(7)])
    out = await agent(model).ainvoke(ask())
    assert len(model.calls) == 4
    assert out["messages"][-1].content == FALLBACK_ANSWER
    assert out["tool_loop_exhausted"] is True
    # Đúng như vòng lặp cũ: tool của vòng thứ 4 vẫn chạy rồi mới dừng.
    assert sum(isinstance(m, ToolMessage) for m in out["messages"]) == 4


async def test_answer_without_tools_does_not_set_exhausted():
    model = scripted([AIMessage("Dạ còn ạ.")])
    out = await agent(model).ainvoke(ask())
    assert out["messages"][-1].content == "Dạ còn ạ."
    assert "tool_loop_exhausted" not in out


async def test_rounds_are_counted_from_the_last_customer_message():
    model = scripted([AIMessage("Dạ mới ạ.")])
    history = [HumanMessage("cũ"), AIMessage("a"), AIMessage("b"), AIMessage("c"), AIMessage("d")]
    out = await agent(model).ainvoke({"messages": [*history, HumanMessage("mới")]})
    assert len(model.calls) == 1
    assert out["messages"][-1].content == "Dạ mới ạ."
    assert "tool_loop_exhausted" not in out


async def test_tool_exception_becomes_the_vietnamese_error_text():
    model = scripted([tool_call("broken_lookup", {"day": "2026-08-07"}), AIMessage("Dạ.")])
    await agent(model, tools=[broken_lookup]).ainvoke(ask())
    tool_msgs = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_msgs] == [TOOL_ERROR_TEXT]


async def test_bad_tool_arguments_also_become_the_vietnamese_error_text():
    """Vòng lặp cũ gọi `tool.ainvoke(args)` và nuốt MỌI lỗi, kể cả sai tham số.
    ToolNode tự đổi lỗi tham số thành chữ tiếng Anh (dump pydantic) — giữ như cũ."""
    model = scripted([tool_call("fake_lookup", {"wrong": 1}), AIMessage("Dạ.")])
    await agent(model).ainvoke(ask())
    tool_msgs = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_msgs] == [TOOL_ERROR_TEXT]


async def test_unknown_tool_does_not_crash():
    model = scripted([tool_call("khong_ton_tai", {}), AIMessage("Dạ.")])
    out = await agent(model).ainvoke(ask())
    assert out["messages"][-1].content == "Dạ."
    assert any(isinstance(m, ToolMessage) and m.content == "Không có tool tên khong_ton_tai."
               for m in out["messages"])


async def test_parallel_tool_calls_off_when_tools_exist():
    model = scripted([AIMessage("Dạ.")])
    await agent(model).ainvoke(ask())
    assert model.bind_kwargs[0]["parallel_tool_calls"] is False


async def test_no_parallel_flag_without_tools():
    model = scripted([AIMessage("Dạ.")])
    await agent(model, tools=[]).ainvoke(ask())
    assert all("parallel_tool_calls" not in k for k in model.bind_kwargs)


def test_middleware_keeps_no_per_turn_state_on_self():
    """Ràng buộc toàn cục: bộ đếm vòng đi qua state, không qua `self`."""
    for mw in all_mw():
        assert set(vars(mw)) <= {"max_rounds"}, vars(mw)
