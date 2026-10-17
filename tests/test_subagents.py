"""Node agent (`make_agent_node(build_agent(...))`) — chạy trên `create_agent`.

Các khẳng định thứ tự message chi tiết nằm ở `test_mw_layout.py`; ở đây chỉ
giữ MỘT test đầu-cuối chứng minh middleware bố cục thật sự được lắp vào agent.
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agents.booking_graph.agents import (FALLBACK_ANSWER, MAX_TOOL_ROUNDS,
                                             build_agent, make_agent_node)
from app.models.conversation import ConversationSlots
from tests.fakes import scripted, tool_call


@tool
async def fake_lookup(day: str) -> str:
    """Tra cứu giả lập."""
    return f"còn trống lúc 3 giờ chiều ngày {day}"


def tool_call_message(day="2026-08-07", id="call_1"):
    return tool_call("fake_lookup", {"day": day}, id=id)


def a_state(text="mai còn trống không con"):
    return {
        "messages": [HumanMessage(content=text)],
        "user_id": "u1",
        "context_block": "Bạn đang nói chuyện với: Cô Lan (0912345678).",
    }


def node_for(model, tools=(fake_lookup,)):
    return make_agent_node(build_agent("prompt", list(tools), model=model))


async def test_answers_without_calling_tools_when_not_needed():
    node = node_for(scripted([AIMessage(content="Dạ chủ tiệm đang rảnh ạ.")]))
    assert (await node(a_state()))["draft"] == "Dạ chủ tiệm đang rảnh ạ."


async def test_runs_a_tool_then_answers():
    node = node_for(scripted([tool_call_message(), AIMessage(content="Dạ mai còn trống 3 giờ chiều ạ.")]))
    result = await node(a_state())
    # Tool message KHÔNG quay về `messages` của graph ngoài: node chỉ trả draft.
    assert result == {"draft": "Dạ mai còn trống 3 giờ chiều ạ.", "proposal_id": None}


async def test_tool_result_is_fed_back_to_the_model():
    model = scripted([tool_call_message(), AIMessage(content="xong")])
    await node_for(model)(a_state())
    assert any("còn trống lúc 3 giờ chiều" in str(m.content) for m in model.calls[-1])


async def test_state_sits_right_after_the_system_prompt():
    """Thứ tự khoá cứng (CONTEXT.md bẫy #9), đi đầu-cuối qua agent thật:
    system → [summary + slots] → lịch sử → bối cảnh → câu khách → tool exchange."""
    model = scripted([tool_call_message(), AIMessage(content="Dạ.")])
    state = {
        **a_state("còn không em"),
        "summary": ["Khách muốn làm tóc."],
        "slots": ConversationSlots(intent="book", service="làm móng bột"),
        "messages": [HumanMessage(content="hôm qua"), AIMessage(content="dạ"),
                     HumanMessage(content="còn không em")],
    }

    await node_for(model)(state)

    first, second = model.calls
    assert isinstance(first[0], SystemMessage) and first[0].content == "prompt"
    assert first[1].content.startswith("Diễn biến phần trước của cuộc trò chuyện hôm nay:")
    assert "- Khách muốn làm tóc." in first[1].content
    assert "- Dịch vụ: làm móng bột" in first[1].content          # CÙNG một message
    assert [m.content for m in first[2:]] == [
        "hôm qua", "dạ", "Bạn đang nói chuyện với: Cô Lan (0912345678).", "còn không em"]
    # Vòng 2: tool exchange của lượt này đứng SAU câu khách, bối cảnh vẫn áp sát câu khách.
    assert [m.content for m in second[2:6]] == [m.content for m in first[2:6]]
    assert second[6].tool_calls and "còn trống lúc 3 giờ chiều" in second[7].content


async def test_tools_are_bound_to_the_model():
    model = scripted([AIMessage(content="xong")])
    await node_for(model)(a_state())
    assert [t.name for t in model.bound_tools] == ["fake_lookup"]


async def test_tool_loop_stops_at_the_limit():
    """Model hỏng gọi tool mãi không được treo cuộc chat của khách."""
    model = scripted([tool_call_message(id=f"c{i}") for i in range(MAX_TOOL_ROUNDS + 3)])
    result = await node_for(model)(a_state())
    assert result["draft"] == FALLBACK_ANSWER
    assert len(model.calls) == MAX_TOOL_ROUNDS


async def test_unknown_tool_name_does_not_crash():
    bad = tool_call("khong_ton_tai", {}, id="c1")
    node = node_for(scripted([bad, AIMessage(content="Dạ con xin lỗi ạ.")]))
    assert (await node(a_state()))["draft"] == "Dạ con xin lỗi ạ."


async def test_llm_node_returns_a_draft_not_an_answer():
    out = await node_for(scripted([AIMessage(content="Dạ.")]), tools=[])(a_state("chào"))
    assert out == {"draft": "Dạ.", "proposal_id": None}


async def test_empty_model_reply_falls_back():
    out = await node_for(scripted([AIMessage(content="")]), tools=[])(a_state("chào"))
    assert out == {"draft": FALLBACK_ANSWER, "proposal_id": None}


def test_build_agent_tags_the_real_model_respond(monkeypatch):
    """F10: tag `respond` phải do `build_agent` gắn khi dựng model thật, không
    phải do model giả mang sẵn — sai là token không stream (màn hình trống tới
    lúc complete)."""
    seen = []

    def fake_build(**kw):
        seen.append(kw)
        return scripted([], tags=kw.get("tags") or ())

    monkeypatch.setattr("app.agents.booking_graph.agents.build_chat_model", fake_build)
    agent = build_agent("p", [])
    assert seen == []                    # dựng lười: chưa gọi thì chưa dựng model
    agent._get()                         # lần dùng đầu tiên mới dựng
    assert seen == [{"tags": ["respond"], "temperature": 0.2}]


async def test_lazy_agent_builds_its_model_once(monkeypatch):
    seen = []

    def fake_build(**kw):
        seen.append(kw)
        return scripted([AIMessage("Dạ."), AIMessage("Dạ lần hai.")], tags=kw.get("tags") or ())

    monkeypatch.setattr("app.agents.booking_graph.agents.build_chat_model", fake_build)
    node = make_agent_node(build_agent("p", []))
    assert (await node(a_state()))["draft"] == "Dạ."
    assert (await node(a_state()))["draft"] == "Dạ lần hai."
    assert len(seen) == 1
