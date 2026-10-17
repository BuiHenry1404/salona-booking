from typing import Any, Awaitable, Callable, List, Sequence

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


class _LazyAgent:
    """Agent dựng ở lần gọi ĐẦU TIÊN, không phải lúc `build_graph`.

    `build_graph` chạy mỗi lượt và dựng 3 agent, nhưng mỗi lượt chỉ chạy một.
    Dựng model ngay thì (a) mất ~25 ms/model đồng bộ trên event loop, (b) thiếu
    key LLM là `build_graph` nổ — mất luôn nhánh confirm→phrase vốn chạy được
    như A, và test dựng graph phải có key thật. A dựng model lúc node chạy;
    giữ đúng như vậy.

    Cache trong `self` là cấu hình (model + graph đã compile), không phải trạng
    thái lượt chat — graph ngoài dựng lại mỗi lượt.
    """

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._agent = None

    def _get(self):
        if self._agent is None:
            self._agent = self._factory()
        return self._agent

    async def ainvoke(self, *args, **kwargs):
        return await self._get().ainvoke(*args, **kwargs)

    # KHÔNG có `__getattr__` uỷ quyền: LangGraph `compile()` dò subgraph bằng
    # `hasattr` trên closure của node — uỷ quyền là dựng model ngay lúc
    # build_graph, mất hết tác dụng của việc dựng lười.


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

    Không truyền `model` thì model thật chỉ được dựng ở lần gọi đầu (`_LazyAgent`).
    """
    middleware = [
        *extra_middleware,
        MessageLayoutMiddleware(),
        NoParallelToolsMiddleware(),
        ToolRoundLimitMiddleware(),
        ToolErrorMiddleware(),
    ]

    def factory(chat_model=None):
        return create_agent(
            chat_model or build_chat_model(tags=[tag], temperature=0.2),
            tools,
            system_prompt=prompt,
            middleware=middleware,
            checkpointer=False,
        )

    if model is not None:
        return factory(model)
    return _LazyAgent(factory)


def make_agent_node(agent) -> Callable[[GraphState], Awaitable[dict]]:
    """Node LangGraph bọc một agent `build_agent`.

    `messages` là cả thread trong ngày; `HistoryWindowMiddleware` (trong
    `extra_middleware` của graph) cắt lại đúng cửa sổ A gửi model.

    Node không set `answer` — chỉ `draft`; `guard` mới quyết câu cuối. Tool
    message của lượt này ở lại trong agent con, KHÔNG quay về `messages` ngoài.
    """
    async def node(state: GraphState) -> dict:
        out = await agent.ainvoke({
            "messages": state["messages"],
            "context_block": state.get("context_block", ""),
            "summary": state.get("summary") or [],
            "slots": state.get("slots"),
            # HistoryWindowMiddleware đọc mốc nén từ đây — thiếu là tin đã nén
            # bị gửi lại nguyên văn mỗi lượt (F2).
            "history_after": state.get("history_after"),
        })
        messages = out.get("messages") or []
        reply = messages[-1] if messages else None
        return {"draft": getattr(reply, "content", "") or FALLBACK_ANSWER}

    return node
