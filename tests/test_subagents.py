import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.agents.booking_graph.agents import MAX_TOOL_ROUNDS, make_subagent_node


@tool
async def fake_lookup(day: str) -> str:
    """Tra cứu giả lập."""
    return f"còn trống lúc 3 giờ chiều ngày {day}"


class ScriptedModel:
    """Phát lần lượt các phản hồi đã dựng sẵn, ghi lại mọi thứ nhận được."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


def tool_call_message(day="2026-08-07"):
    return AIMessage(
        content="",
        tool_calls=[{"name": "fake_lookup", "args": {"day": day}, "id": "call_1"}],
    )


@pytest.fixture
def patch_model(monkeypatch):
    def _install(replies):
        model = ScriptedModel(replies)
        monkeypatch.setattr(
            "app.agents.booking_graph.agents.build_chat_model", lambda **kw: model
        )
        return model
    return _install


def a_state(text="mai còn trống không con"):
    return {
        "messages": [HumanMessage(content=text)],
        "user_id": "u1",
        "context_block": "Bạn đang nói chuyện với: Cô Lan (0912345678).",
    }


@pytest.mark.asyncio
async def test_answers_without_calling_tools_when_not_needed(patch_model):
    patch_model([AIMessage(content="Dạ chủ tiệm đang rảnh ạ.")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    assert (await node(a_state()))["answer"] == "Dạ chủ tiệm đang rảnh ạ."


@pytest.mark.asyncio
async def test_runs_a_tool_then_answers(patch_model):
    patch_model([tool_call_message(), AIMessage(content="Dạ mai còn trống 3 giờ chiều ạ.")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    result = await node(a_state())
    assert result["answer"] == "Dạ mai còn trống 3 giờ chiều ạ."


@pytest.mark.asyncio
async def test_tool_result_is_fed_back_to_the_model(patch_model):
    model = patch_model([tool_call_message(), AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node(a_state())

    last_call = model.calls[-1]
    assert any("còn trống lúc 3 giờ chiều" in str(getattr(m, "content", "")) for m in last_call)


@pytest.mark.asyncio
async def test_context_block_is_the_last_message_before_the_question(patch_model):
    """Bố cục prompt: System → lịch sử → bối cảnh → câu hỏi mới.

    Đây là thứ tự `CONTEXT.md` bẫy #9 đã chốt. Lời khách phải là thứ CUỐI
    model đọc: model bắt chước giọng người đối thoại, nên để một khối trạng
    thái đứng cuối là nó sinh ra giọng biểu mẫu.

    Tiền tố cache được vẫn chỉ là `SystemMessage` — lịch sử vốn đổi mỗi
    lượt — nên đổi chỗ này không mất gì.
    """
    model = patch_model([AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node(a_state("mai còn trống không em"))

    contents = [str(getattr(m, "content", "")) for m in model.calls[0]]
    assert contents[0] == "prompt"                       # system đứng đầu, ổn định
    assert "0912345678" in contents[-2]                  # bối cảnh áp chót
    assert contents[-1] == "mai còn trống không em"      # lời khách đứng cuối


@pytest.mark.asyncio
async def test_history_stays_in_order_before_the_context_block(patch_model):
    """Lịch sử không được xáo: model đọc mạch hội thoại theo đúng thứ tự
    xảy ra, rồi mới tới bối cảnh, rồi tới câu mới."""
    model = patch_model([AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node({
        "messages": [
            HumanMessage(content="câu cũ"),
            AIMessage(content="đáp cũ"),
            HumanMessage(content="câu mới"),
        ],
        "user_id": "u1",
        "context_block": "Bạn đang nói chuyện với: Cô Lan (0912345678).",
    })

    contents = [str(getattr(m, "content", "")) for m in model.calls[0]]
    assert contents == [
        "prompt", "câu cũ", "đáp cũ",
        "Bạn đang nói chuyện với: Cô Lan (0912345678).",
        "câu mới",
    ]


@pytest.mark.asyncio
async def test_tools_are_bound_to_the_model(patch_model):
    model = patch_model([AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node(a_state())
    assert [t.name for t in model.bound_tools] == ["fake_lookup"]


@pytest.mark.asyncio
async def test_tool_loop_stops_at_the_limit(patch_model):
    """Model hỏng gọi tool mãi không được treo cuộc chat của khách."""
    patch_model([tool_call_message() for _ in range(MAX_TOOL_ROUNDS + 3)])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    result = await node(a_state())
    assert result["answer"]


@pytest.mark.asyncio
async def test_unknown_tool_name_does_not_crash(patch_model):
    bad = AIMessage(content="", tool_calls=[{"name": "khong_ton_tai", "args": {}, "id": "c1"}])
    patch_model([bad, AIMessage(content="Dạ con xin lỗi ạ.")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    assert (await node(a_state()))["answer"] == "Dạ con xin lỗi ạ."
