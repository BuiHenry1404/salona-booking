# Task 7 · Hai subagent chuyên trách

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/agents.py`, `tests/test_subagents.py`

**Interfaces:**
- Consumes: `make_status_tools`, `make_booking_tools` (task 5), `STATUS_PROMPT`, `BOOKING_PROMPT` (task 6), `build_chat_model` (task 1), `GraphState` (task 4)
- Produces:
  - `make_subagent_node(prompt: str, tools: list[BaseTool], tag: str) -> Callable[[GraphState], Awaitable[dict]]`
  - `MAX_TOOL_ROUNDS: int = 4`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_subagents.py`:

```python
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
        "recalled": [],
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
    """Bố cục prompt: khối bối cảnh đổi mỗi lượt nên phải đứng sát cuối,
    không nhét vào system prompt — nếu không prompt cache không bao giờ trúng."""
    model = patch_model([AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node(a_state())

    contents = [str(getattr(m, "content", "")) for m in model.calls[0]]
    assert contents[0] == "prompt"                    # system đứng đầu, ổn định
    assert "0912345678" in contents[-1]               # bối cảnh đứng cuối


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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_subagents.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.agents'`

- [ ] **Step 3: Viết `app/agents/booking_graph/agents.py`**

```python
from typing import Awaitable, Callable, List

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4
FALLBACK_ANSWER = "Dạ con chưa tra được, cô chú gọi trực tiếp cho tiệm giúp con nhé ạ."


def make_subagent_node(
    prompt: str, tools: List[BaseTool], tag: str
) -> Callable[[GraphState], Awaitable[dict]]:
    """Một node LangGraph chạy vòng lặp gọi tool rồi trả lời.

    `tag` gắn vào model để bộ phát sự kiện lọc được token: chỉ node sinh câu trả
    lời cuối mới mang tag "respond", nên token định tuyến không lọt ra màn hình.
    """
    by_name = {t.name: t for t in tools}

    async def node(state: GraphState) -> dict:
        model = build_chat_model(tags=[tag], temperature=0.2).bind_tools(tools)

        # Bố cục theo độ ổn định: system (tĩnh, được cache) → lịch sử →
        # khối bối cảnh (đổi mỗi lượt) đặt sát cuối, ngay trước câu hỏi mới.
        messages = [
            SystemMessage(content=prompt),
            *state["messages"],
            HumanMessage(content=state.get("context_block", "")),
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            reply = await model.ainvoke(messages)
            messages.append(reply)

            calls = getattr(reply, "tool_calls", None)
            if not calls:
                return {"answer": reply.content or FALLBACK_ANSWER}

            for call in calls:
                tool = by_name.get(call["name"])
                if tool is None:
                    output = f"Không có tool tên {call['name']}."
                    logger.warning("unknown_tool_requested", extra={"name": call["name"]})
                else:
                    try:
                        output = await tool.ainvoke(call["args"])
                    except Exception as exc:
                        logger.warning("tool_failed",
                                       extra={"name": call["name"], "error": str(exc)})
                        output = "Tra cứu không được, thử lại giúp con ạ."
                messages.append(ToolMessage(content=str(output), tool_call_id=call["id"]))

        logger.warning("tool_loop_exhausted", extra={"tag": tag})
        return {"answer": FALLBACK_ANSWER}

    return node
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_subagents.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/agents.py tests/test_subagents.py
git commit -m "feat: subagent node with bounded tool loop and stability-ordered prompt"
```
