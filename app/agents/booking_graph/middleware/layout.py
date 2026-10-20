"""Bố cục message gửi model — CONTEXT.md bẫy #9, #6, #20/#22.

System → [summary + slots] → lịch sử → khối bối cảnh → câu khách → tool
exchange của lượt này.

Lời khách phải là thứ CUỐI model đọc trước khi trả lời. Model bắt chước giọng
người đối thoại; để khối trạng thái đứng cuối là nó đáp lại bằng giọng biểu mẫu
("em giữ chỗ cho dịch vụ làm tóc").

Middleware chỉ sửa thứ GỬI model (`wrap_model_call`), không sửa state: khối bối
cảnh và khối trạng thái đổi mỗi lượt, không được lọt vào `messages` của agent.
"""
from typing import Any, Awaitable, Callable, List

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest
from langchain_core.messages import AnyMessage, HumanMessage
from typing_extensions import NotRequired

from app.agents.booking_graph.context import render_slots

# Tiêu đề cố định do code sinh — tiếng Việt vì là DỮ LIỆU, cùng lối với khối
# bối cảnh, không phải chỉ dẫn. `agents.py` re-export lại tên này.
STATE_HEADER = "Diễn biến phần trước của cuộc trò chuyện hôm nay:"


class LayoutState(AgentState):
    context_block: NotRequired[str]
    summary: NotRequired[List[str]]
    slots: NotRequired[Any]


def layout_messages(
    messages: List[AnyMessage], *, context_block: str, summary: List[str], slots
) -> List[AnyMessage]:
    """Xếp lại `messages` (không kèm system) theo thứ tự bẫy #9.

    Câu khách = HumanMessage CUỐI CÙNG. Phần trước nó là lịch sử, phần sau nó
    là tool exchange của chính lượt này (AIMessage gọi tool + ToolMessage).
    Không có HumanMessage nào thì coi tất cả là lịch sử.
    """
    last_human = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)),
        None,
    )
    if last_human is None:
        history, tail = list(messages), []
    else:
        history, tail = list(messages[:last_human]), list(messages[last_human:])

    # Khối trạng thái đứng NGAY SAU system: đổi vài lượt một lần, ổn định hơn
    # khối bối cảnh (đổi mỗi lượt) nên đặt trước để tiền tố cache sống lâu.
    # Summary và slots đi CHUNG một message — tách đôi là thêm một ranh giới
    # nữa cho thứ tự bẫy #9 có thể trượt.
    parts = []
    if summary:
        parts.append(STATE_HEADER + "\n" + "\n".join(f"- {s}" for s in summary))
    rendered = render_slots(slots)
    if rendered:
        parts.append(rendered)
    state_messages = [HumanMessage(content="\n\n".join(parts))] if parts else []

    return [
        *state_messages,
        *history,
        HumanMessage(content=context_block),
        *tail,
    ]


class MessageLayoutMiddleware(AgentMiddleware):
    state_schema = LayoutState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ):
        state = request.state
        return await handler(request.override(messages=layout_messages(
            request.messages,
            context_block=state.get("context_block", ""),
            summary=state.get("summary") or [],
            slots=state.get("slots"),
        )))
