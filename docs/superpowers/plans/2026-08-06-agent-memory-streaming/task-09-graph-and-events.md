# Task 9 · Ráp đồ thị và phát sự kiện streaming

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/graph.py`, `app/agents/booking_graph/events.py`, `tests/test_graph_events.py`
- Modify: `app/agents/booking_graph/__init__.py`

**Interfaces:**
- Consumes: mọi thứ từ task 4–8
- Produces:
  - `graph.py`: `build_graph(db, user) -> CompiledGraph`
  - `events.py`: `AgentEvent` (dataclass: `type: str`, `data: dict`), `run_turn(db, user, question) -> AsyncIterator[AgentEvent]`
  - Loại sự kiện: `turn_started`, `tool_started`, `tool_finished`, `token`, `complete`, `error`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_graph_events.py`:

```python
import pytest

from app.agents.booking_graph.events import (RESPOND_TAG, AgentEvent,
                                             translate_langchain_event)


def lc_event(name, tags=None, **rest):
    return {"event": name, "tags": tags or [], "name": rest.pop("node_name", "x"), **rest}


def test_token_from_the_respond_node_is_forwarded():
    class Chunk:
        content = "Dạ"

    events = translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[RESPOND_TAG], data={"chunk": Chunk()})
    )
    assert [(e.type, e.data["text"]) for e in events] == [("token", "Dạ")]


def test_token_from_the_supervisor_is_dropped():
    """Token của supervisor là JSON định tuyến — lọt ra màn hình là rác chạy ngang."""
    class Chunk:
        content = "booking"

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=["supervisor"], data={"chunk": Chunk()})
    ) == []


def test_token_without_any_tag_is_dropped():
    class Chunk:
        content = "?"

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[], data={"chunk": Chunk()})
    ) == []


def test_empty_token_is_dropped():
    class Chunk:
        content = ""

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[RESPOND_TAG], data={"chunk": Chunk()})
    ) == []


def test_tool_start_becomes_tool_started_with_the_raw_name():
    """Backend gửi tên thô; dịch sang tiếng Việt là việc của frontend."""
    events = translate_langchain_event(lc_event("on_tool_start", node_name="find_free_slots"))
    assert [(e.type, e.data["name"]) for e in events] == [("tool_started", "find_free_slots")]


def test_tool_end_becomes_tool_finished_ok():
    events = translate_langchain_event(lc_event("on_tool_end", node_name="find_free_slots"))
    assert events[0].type == "tool_finished"
    assert events[0].data == {"name": "find_free_slots", "ok": True}


def test_tool_error_becomes_tool_finished_not_ok():
    events = translate_langchain_event(lc_event("on_tool_error", node_name="propose_appointment"))
    assert events[0].data == {"name": "propose_appointment", "ok": False}


def test_unrelated_events_produce_nothing():
    assert translate_langchain_event(lc_event("on_chain_start")) == []
    assert translate_langchain_event(lc_event("on_retriever_end")) == []


def test_agent_event_is_json_serialisable():
    import json
    event = AgentEvent(type="token", data={"text": "Dạ"})
    assert json.loads(json.dumps(event.data)) == {"text": "Dạ"}
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_graph_events.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.events'`

- [ ] **Step 3: Viết `app/agents/booking_graph/graph.py`**

```python
from langgraph.graph import END, START, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import make_subagent_node
from app.agents.booking_graph.confirm import make_confirm_node
from app.agents.booking_graph.prompts import BOOKING_PROMPT, STATUS_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.supervisor import (refuse, route_from_state,
                                                 supervise)
from app.agents.booking_graph.tools import (make_booking_tools,
                                            make_status_tools)
from app.models.user import User

RESPOND_TAG = "respond"


def build_graph(db: AsyncIOMotorDatabase, user: User):
    """Đồ thị cho một khách cụ thể.

    Dựng lại mỗi lượt vì tool đóng kín `user` trong closure — đó là cách user_id
    không bao giờ trở thành tham số mà AI có thể điều khiển.
    """
    graph = StateGraph(GraphState)

    graph.add_node("supervisor", supervise)
    graph.add_node("refuse", refuse)
    graph.add_node("confirm", make_confirm_node(db, user))
    graph.add_node(
        "status",
        make_subagent_node(STATUS_PROMPT, make_status_tools(db, user), tag=RESPOND_TAG),
    )
    graph.add_node(
        "booking",
        make_subagent_node(BOOKING_PROMPT, make_booking_tools(db, user), tag=RESPOND_TAG),
    )

    # Nhánh tắt: có pending_confirmation thì bỏ qua supervisor hoàn toàn.
    graph.add_conditional_edges(
        START, route_from_state, {"confirm": "confirm", "supervisor": "supervisor"}
    )
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["route"],
        {"booking": "booking", "status": "status", "refuse": "refuse"},
    )

    for node in ("confirm", "refuse", "status", "booking"):
        graph.add_edge(node, END)

    return graph.compile()
```

- [ ] **Step 4: Viết `app/agents/booking_graph/events.py`**

```python
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List

from langchain_core.messages import AIMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.graph import RESPOND_TAG, build_graph
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.models.user import User
from app.services.conversation import ConversationService

logger = get_logger(__name__)


@dataclass
class AgentEvent:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


def translate_langchain_event(event: dict) -> List[AgentEvent]:
    """Dịch sự kiện thô của LangGraph sang sự kiện gửi cho giao diện.

    Điểm quan trọng nhất: CHỈ chuyển tiếp token mang tag "respond". Không lọc thì
    token JSON định tuyến của supervisor sẽ chạy ngang màn hình khách.
    """
    kind = event.get("event")

    if kind == "on_chat_model_stream":
        if RESPOND_TAG not in (event.get("tags") or []):
            return []
        chunk = (event.get("data") or {}).get("chunk")
        text = getattr(chunk, "content", "") or ""
        return [AgentEvent("token", {"text": text})] if text else []

    if kind == "on_tool_start":
        return [AgentEvent("tool_started", {"name": event.get("name", "")})]

    if kind == "on_tool_end":
        return [AgentEvent("tool_finished", {"name": event.get("name", ""), "ok": True})]

    if kind == "on_tool_error":
        return [AgentEvent("tool_finished", {"name": event.get("name", ""), "ok": False})]

    return []


async def run_turn(
    db: AsyncIOMotorDatabase, user: User, question: str
) -> AsyncIterator[AgentEvent]:
    """Chạy một lượt chat và phát sự kiện theo thứ tự thời gian thực.

    """
    from app.agents.booking_graph.context import load_context

    user_id = str(user.id)
    conversations = ConversationService(db)

    yield AgentEvent("turn_started")

    try:
        context = await load_context(db, user, question)
        history = [
            HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
            for m in context["history"]
        ]

        state = {
            "messages": [*history, HumanMessage(content=question)],
            "user_id": user_id,
            "context_block": context["context_block"],
            "pending_confirmation": context["pending_confirmation"],
        }

        graph = build_graph(db, user)
        # Langfuse v3: handler không mang danh tính, danh tính đi qua metadata.
        # Mỗi khách có đúng một conversation nên session_id trùng user_id.
        config = {
            "callbacks": get_callbacks(),
            "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id),
        }

        final_state: Dict[str, Any] = {}
        async for raw in graph.astream_events(state, config=config, version="v2"):
            for event in translate_langchain_event(raw):
                yield event
            if raw.get("event") == "on_chain_end" and isinstance(raw.get("data"), dict):
                output = raw["data"].get("output")
                if isinstance(output, dict) and "answer" in output:
                    final_state = output

        answer = final_state.get("answer", "")
        if answer:
            await conversations.append(user_id, "user", question)
            await conversations.append(user_id, "assistant", answer)

        yield AgentEvent("complete", {"answer": answer})

    except Exception as exc:
        logger.error("agent_turn_failed", extra={"user_id": user_id, "error": str(exc)})
        yield AgentEvent("error", {
            "message": "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé."
        })
```

- [ ] **Step 5: Xuất API công khai trong `app/agents/booking_graph/__init__.py`**

```python
from app.agents.booking_graph.events import AgentEvent, run_turn

__all__ = ["run_turn", "AgentEvent"]
```

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: `pytest tests/test_graph_events.py -v`
Expected: PASS (9 passed) — quan trọng nhất là `test_token_from_the_supervisor_is_dropped`

- [ ] **Step 7: Commit**

```bash
git add app/agents/booking_graph tests/test_graph_events.py
git commit -m "feat: compile booking graph and translate stream events for the UI"
```
