from typing import Awaitable, Callable, List

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4
FALLBACK_ANSWER = "Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm giúp em nhé ạ."

# Tiêu đề cố định do code sinh — tiếng Việt vì là DỮ LIỆU, cùng lối với khối
# bối cảnh, không phải chỉ dẫn.
STATE_HEADER = "Diễn biến phần trước của cuộc trò chuyện hôm nay:"


def make_subagent_node(
    prompt: str, tools: List[BaseTool], tag: str
) -> Callable[[GraphState], Awaitable[dict]]:
    """Một node LangGraph chạy vòng lặp gọi tool rồi trả lời.

    `tag` gắn vào model để bộ phát sự kiện lọc được token: chỉ node sinh câu trả
    lời cuối mới mang tag "respond", nên token định tuyến không lọt ra màn hình.

    Node không set `answer` — chỉ `draft`; `guard` mới quyết câu cuối.
    """
    by_name = {t.name: t for t in tools}

    async def node(state: GraphState) -> dict:
        model = build_chat_model(tags=[tag], temperature=0.2).bind_tools(tools)

        # Bố cục theo độ ổn định: system (tĩnh, được cache) → lịch sử →
        # khối bối cảnh (đổi mỗi lượt) → câu hỏi mới.
        #
        # Lời khách phải là thứ CUỐI model đọc. Model bắt chước giọng người
        # đối thoại; để khối trạng thái đứng cuối là nó đáp lại bằng giọng
        # biểu mẫu ("em giữ chỗ cho dịch vụ làm tóc"). Đây cũng đúng thứ tự
        # `CONTEXT.md` bẫy #9 đã chốt từ đầu.
        #
        # Cắt lát chịu được `messages` rỗng: khi đó cả hai vế cùng rỗng.
        history, question = state["messages"][:-1], state["messages"][-1:]
        # ConversationState đứng NGAY SAU system: đổi vài lượt một lần, ổn định hơn khối
        # bối cảnh (đổi mỗi lượt) nên đặt trước để tiền tố cache sống lâu.
        summary = state.get("summary") or []
        summary_messages = (
            [HumanMessage(content=STATE_HEADER + "\n" + "\n".join(f"- {b}" for b in summary))]
            if summary else []
        )
        messages = [
            SystemMessage(content=prompt),
            *summary_messages,
            *history,
            HumanMessage(content=state.get("context_block", "")),
            *question,
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            # Gửi một bản chụp: `messages` còn bị nối thêm ngay sau đây, mà
            # LangChain giữ nguyên tham chiếu — trace và test sẽ thấy một danh
            # sách khác với thứ thật sự gửi đi ở lượt đó.
            reply = await model.ainvoke(list(messages))
            messages.append(reply)

            calls = getattr(reply, "tool_calls", None)
            if not calls:
                return {"draft": reply.content or FALLBACK_ANSWER}

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
                        output = "Tra cứu không được, thử lại giúp em ạ."
                messages.append(ToolMessage(content=str(output), tool_call_id=call["id"]))

        logger.warning("tool_loop_exhausted", extra={"tag": tag})
        return {"draft": FALLBACK_ANSWER}

    return node
