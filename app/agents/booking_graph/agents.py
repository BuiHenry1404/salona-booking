from typing import Awaitable, Callable, List, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from app.agents.booking_graph.middleware.layout import (  # noqa: F401 (re-export)
    STATE_HEADER, MessageLayoutMiddleware)
from app.agents.booking_graph.middleware.tools import (  # noqa: F401 (re-export)
    FALLBACK_ANSWER, MAX_TOOL_ROUNDS, TOOL_ERROR_TEXT, NoParallelToolsMiddleware,
    ToolErrorMiddleware, ToolRoundLimitMiddleware)
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model

# Chỉ token mang tag này mới được đẩy ra màn hình khách (events.py lọc theo nó).
RESPOND_TAG = "respond"


def build_agent(
    prompt: str,
    tools: List[BaseTool],
    *,
    tag: str = RESPOND_TAG,
    model=None,
    extra_middleware: Sequence[AgentMiddleware] = (),
):
    """Một agent `create_agent`: gọi tool rồi trả lời.

    `tag` gắn vào model để bộ phát sự kiện lọc được token: chỉ agent sinh câu
    trả lời cuối mới mang tag "respond", nên token định tuyến không lọt ra màn
    hình.

    Thứ tự middleware (ngoài → trong): `extra_middleware` đứng ngoài cùng (M2
    thêm lịch sử, M3 thêm confirm), rồi bố cục message, tắt parallel, giới hạn
    vòng, lỗi tool.

    `checkpointer=False`: agent con không bao giờ ghi checkpoint riêng — state
    lượt chat thuộc graph ngoài.
    """
    return create_agent(
        model or build_chat_model(tags=[tag], temperature=0.2),
        tools,
        system_prompt=prompt,
        middleware=[
            *extra_middleware,
            MessageLayoutMiddleware(),
            NoParallelToolsMiddleware(),
            ToolRoundLimitMiddleware(),
            ToolErrorMiddleware(),
        ],
        checkpointer=False,
    )


def make_agent_node(agent) -> Callable[[GraphState], Awaitable[dict]]:
    """Node LangGraph bọc một agent `build_agent`.

    Node không set `answer` — chỉ `draft`; `guard` mới quyết câu cuối. Tool
    message của lượt này ở lại trong agent con, KHÔNG quay về `messages` ngoài.
    """
    async def node(state: GraphState) -> dict:
        out = await agent.ainvoke({
            "messages": state["messages"],
            "context_block": state.get("context_block", ""),
            "summary": state.get("summary") or [],
            "slots": state.get("slots"),
        })
        messages = out.get("messages") or []
        reply = messages[-1] if messages else None
        return {"draft": getattr(reply, "content", "") or FALLBACK_ANSWER}

    return node
