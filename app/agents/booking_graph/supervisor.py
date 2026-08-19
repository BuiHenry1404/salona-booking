from langchain_core.messages import SystemMessage

from app.agents.booking_graph.prompts import REFUSE_MESSAGE, SUPERVISOR_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_ROUTES = {"booking", "status", "refuse"}
SUPERVISOR_HISTORY_TURNS = 4


def route_from_state(state: GraphState) -> str:
    """Nhánh tắt: khách vừa được hỏi xác nhận thì câu trả lời đi thẳng vào thực thi.

    Tiết kiệm một lượt LLM và loại bỏ khả năng supervisor hiểu "ừ" thành ý định mới.
    """
    return "confirm" if state.get("pending_confirmation") else "supervisor"


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định.

    Supervisor KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp tới —
    chỉ vài lượt chat cuối. Nó chỉ cần biết khách đang muốn gì.
    """
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    recent = state["messages"][-SUPERVISOR_HISTORY_TURNS:]

    reply = await model.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent])
    route = (reply.content or "").strip().lower()

    if route not in VALID_ROUTES:
        logger.info("supervisor_fallback", extra={"raw": route[:80]})
        route = "booking"

    return {"route": route}


async def refuse(state: GraphState) -> dict:
    """Câu ngoài chủ đề không bao giờ tới subagent — đây là lớp thứ nhất của
    ràng buộc 'AI chỉ để đặt lịch'."""
    return {"answer": REFUSE_MESSAGE}
