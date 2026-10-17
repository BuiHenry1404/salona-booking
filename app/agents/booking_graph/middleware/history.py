"""Cửa sổ lịch sử cho agent con — chỉ gửi model phần A từng gửi.

Agent nhận NGUYÊN thread trong ngày (`messages` của graph ngoài). Middleware
thay phần trước câu khách cuối bằng `model_history(...)`: bỏ tin đã nén (sau
`history_after` = `covers_until`), cắt theo ngày/30 phút/ngân sách token. Câu
khách và tool exchange của lượt này giữ nguyên.

Đứng NGOÀI `MessageLayoutMiddleware` (`extra_middleware` đi trước), nên bố cục
bẫy #9 xếp trên phần đã cắt. Chỉ sửa thứ gửi model, không sửa state.
"""
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest
from langchain_core.messages import HumanMessage
from typing_extensions import NotRequired

from app.agents.booking_graph.history import model_history


class HistoryState(AgentState):
    history_after: NotRequired[Optional[datetime]]


class HistoryWindowMiddleware(AgentMiddleware):
    state_schema = HistoryState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ):
        messages = request.messages
        last_human = next(
            (i for i in range(len(messages) - 1, -1, -1)
             if isinstance(messages[i], HumanMessage)),
            None,
        )
        if last_human is None:
            return await handler(request)
        history = model_history(messages[:last_human],
                                after=request.state.get("history_after"))
        return await handler(request.override(messages=[*history, *messages[last_human:]]))
