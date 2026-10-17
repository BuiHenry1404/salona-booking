"""Ba lưới an toàn quanh vòng lặp tool — thay đúng phần vòng `for` cũ của
`make_subagent_node`.

- `ToolRoundLimitMiddleware`: tối đa `MAX_TOOL_ROUNDS` lần gọi model mỗi lượt;
  model hỏng gọi tool mãi không được treo cuộc chat của khách.
- `ToolErrorMiddleware`: tool ném lỗi (hoặc model gọi tool không tồn tại) không
  được làm sập lượt; model nhận câu lỗi tiếng Việt cố định như A.
- `NoParallelToolsMiddleware`: tắt parallel tool calls, nhưng CHỈ khi request
  có tool — agent `social` (0 tool) mà gửi cờ này thì Azure trả 400 và mọi câu
  xã giao thành "Máy đang bận".

Không middleware nào giữ bộ đếm hay dữ liệu lượt chat trong `self`: một
instance dùng chung cho mọi lượt, đếm lại từ `state["messages"]` mỗi lần.
"""
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import (AgentMiddleware, AgentState,
                                         ModelRequest, hook_config)
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from typing_extensions import NotRequired

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4
FALLBACK_ANSWER = "Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm giúp em nhé ạ."
TOOL_ERROR_TEXT = "Tra cứu không được, thử lại giúp em ạ."


class LimitState(AgentState):
    tool_loop_exhausted: NotRequired[bool]


def _rounds_this_turn(messages) -> int:
    """Số AIMessage sau HumanMessage cuối = số lần model đã được gọi lượt này."""
    count = 0
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, AIMessage):
            count += 1
    return count


class ToolRoundLimitMiddleware(AgentMiddleware):
    state_schema = LimitState

    def __init__(self, max_rounds: int = MAX_TOOL_ROUNDS):
        super().__init__()
        self.max_rounds = max_rounds

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:
        if _rounds_this_turn(state["messages"]) < self.max_rounds:
            return None
        logger.warning("tool_loop_exhausted", extra={"rounds": self.max_rounds})
        return {
            "jump_to": "end",
            "messages": [AIMessage(content=FALLBACK_ANSWER)],
            "tool_loop_exhausted": True,
        }


class ToolErrorMiddleware(AgentMiddleware):
    """Trùng tên với `langchain.agents.middleware.ToolErrorMiddleware` của thư
    viện nhưng không phải nó: bản thư viện để lỗi tham số cho ToolNode tự đổi
    thành chữ tiếng Anh. Ở đây MỌI lỗi tool đều thành `TOOL_ERROR_TEXT`, đúng
    như vòng lặp cũ (`except Exception` quanh `tool.ainvoke`)."""

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ):
        call = request.tool_call
        if request.tool is None:
            logger.warning("unknown_tool_requested", extra={"name": call["name"]})
            return ToolMessage(content=f"Không có tool tên {call['name']}.",
                               tool_call_id=call["id"], name=call["name"])
        try:
            result = await handler(request)
        except GraphBubbleUp:
            # interrupt / Command của LangGraph là luồng điều khiển, không phải lỗi.
            raise
        except Exception as exc:
            logger.warning("tool_failed", extra={"name": call["name"], "error": str(exc)})
            return ToolMessage(content=TOOL_ERROR_TEXT, tool_call_id=call["id"],
                               name=call["name"], status="error")
        if isinstance(result, ToolMessage) and result.status == "error":
            # ToolNode đã tự bắt lỗi tham số (ToolInvocationError) và trả dump
            # pydantic tiếng Anh; A đưa model câu tiếng Việt cố định.
            logger.warning("tool_failed", extra={"name": call["name"], "error": str(result.content)})
            return ToolMessage(content=TOOL_ERROR_TEXT, tool_call_id=call["id"],
                               name=call["name"], status="error")
        return result


class NoParallelToolsMiddleware(AgentMiddleware):
    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ):
        if request.tools:
            request = request.override(
                model_settings={**request.model_settings, "parallel_tool_calls": False})
        return await handler(request)
