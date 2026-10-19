from langchain_core.messages import SystemMessage

from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.prompts import SUPERVISOR_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_ROUTES = {"booking", "shop", "social"}
SUPERVISOR_HISTORY_TURNS = 4


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định.

    Supervisor KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp tới —
    chỉ vài lượt chat cuối. Nó chỉ cần biết khách đang muốn gì.
    """
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    # Vài lượt cuối của CỬA SỔ lịch sử (như A), không phải của cả thread:
    # tin đã nén hay của hôm qua không được kéo định tuyến.
    *earlier, question = state["messages"]
    window = model_history(earlier, after=state.get("history_after"),
                           now=state.get("asked_at"))
    recent = [*window, question][-SUPERVISOR_HISTORY_TURNS:]

    reply = await model.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent])
    route = (reply.content or "").strip().lower()

    if route not in VALID_ROUTES:
        logger.info("supervisor_fallback", extra={"raw": route[:80]})
        route = "booking"

    return {"route": route}

