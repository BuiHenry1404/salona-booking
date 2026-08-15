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
            # Gửi một bản chụp: `messages` còn bị nối thêm ngay sau đây, mà
            # LangChain giữ nguyên tham chiếu — trace và test sẽ thấy một danh
            # sách khác với thứ thật sự gửi đi ở lượt đó.
            reply = await model.ainvoke(list(messages))
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
