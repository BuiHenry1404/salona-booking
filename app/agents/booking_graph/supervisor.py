import asyncio

from langchain_core.messages import SystemMessage

from app.agents.booking_graph.context import live_queue
from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.prompts import SUPERVISOR_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_ROUTES = {"booking", "shop", "social"}
SUPERVISOR_HISTORY_TURNS = 4

# Ngân sách RIÊNG của bước định tuyến (review M4 I-1, lỗi live 1): như các bộ
# phân loại khác (bẫy #10), `wait_for` 8 giây bọc CẢ bước, kể cả 1 lần thử lại
# của SDK. Trước đây supervisor mang timeout 20 giây × 3 lần (retry mặc định
# SDK) ≈ 61 giây > trần lượt 60 giây: một lần Azure treo là mất cả lượt, agent
# không bao giờ tới lượt. Giờ treo/lỗi → route "booking" (nhãn hoà của prompt).
SUPERVISOR_TIMEOUT_SECONDS = 8
SUPERVISOR_MAX_RETRIES = 1


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định.

    Supervisor KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp tới —
    chỉ vài lượt chat cuối. Nó chỉ cần biết khách đang muốn gì.
    """
    if live_queue(state.get("queued")):
        # Đang đặt lần lượt (lỗi live B2 #2): còn lịch chờ đề xuất thì mọi tin
        # vào booking — "ừ" sau câu mời lịch kế không được rơi vào social.
        logger.info("supervisor_queue_route")
        return {"route": "booking"}

    # Vài lượt cuối của CỬA SỔ lịch sử (như A), không phải của cả thread:
    # tin đã nén hay của hôm qua không được kéo định tuyến.
    *earlier, question = state["messages"]
    window = model_history(earlier, after=state.get("history_after"),
                           now=state.get("asked_at"))
    recent = [*window, question][-SUPERVISOR_HISTORY_TURNS:]

    try:
        model = build_chat_model(tags=["supervisor"], temperature=0.0,
                                 timeout=SUPERVISOR_TIMEOUT_SECONDS,
                                 max_retries=SUPERVISOR_MAX_RETRIES)
        reply = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent]),
            timeout=SUPERVISOR_TIMEOUT_SECONDS)
    except Exception as exc:
        # asyncio.TimeoutError là Exception (3.11+). CancelledError (lượt mới
        # thay) KHÔNG bị nuốt: nó là BaseException.
        logger.warning("supervisor_failed", extra={"error": str(exc)[:200],
                                                   "error_type": type(exc).__name__})
        return {"route": "booking"}
    route = (reply.content or "").strip().lower()

    if route not in VALID_ROUTES:
        logger.info("supervisor_fallback", extra={"raw": route[:80]})
        route = "booking"

    return {"route": route}

