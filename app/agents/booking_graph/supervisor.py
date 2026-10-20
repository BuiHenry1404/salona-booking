import asyncio
from typing import Literal

from langchain_core.messages import SystemMessage
from pydantic import BaseModel

from app.agents.booking_graph.context import live_queue
from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.prompts import SUPERVISOR_PROMPT
from app.agents.booking_graph.relatives import asks_if_relative_free, defers
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

Route = Literal["booking", "shop", "social"]
VALID_ROUTES = set(Route.__args__)
SUPERVISOR_HISTORY_TURNS = 4


class RouteDecision(BaseModel):
    """Nhãn định tuyến qua structured output (json_schema), như các bộ phân
    loại khác (`ReplyDecision`, `ParsedTime`): model không thể trả chữ ngoài
    ba nhãn sống, nên không còn bước parse chữ tự do rồi đoán."""
    route: Route

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

    text = str(getattr(state["messages"][-1], "content", "") or "")
    if asks_if_relative_free(text) or defers(text):
        # Lỗi live B3 #2: "nó có rảnh không" (đứa cháu) từng thành câu hỏi
        # chủ tiệm. Hỏi người nhà rảnh không / hẹn hỏi rồi nhắn lại là chuyện
        # lịch của khách: booking (có dòng [relative]/[deferral]).
        logger.info("supervisor_relative_route")
        return {"route": "booking"}

    # Vài lượt cuối của CỬA SỔ lịch sử (như A), không phải của cả thread:
    # tin đã nén hay của hôm qua không được kéo định tuyến.
    *earlier, question = state["messages"]
    window = model_history(earlier, after=state.get("history_after"),
                           now=state.get("asked_at"))
    recent = [*window, question][-SUPERVISOR_HISTORY_TURNS:]

    try:
        # streaming=False: nhãn JSON không có gì để hiện dần — không trông vào
        # mỗi bộ lọc tag để giữ nó khỏi màn hình khách (bẫy #8).
        model = build_chat_model(tags=["supervisor"], temperature=0.0, streaming=False,
                                 timeout=SUPERVISOR_TIMEOUT_SECONDS,
                                 max_retries=SUPERVISOR_MAX_RETRIES)
        structured = model.with_structured_output(RouteDecision)
        result = await asyncio.wait_for(
            structured.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent]),
            timeout=SUPERVISOR_TIMEOUT_SECONDS)
        if not isinstance(result, RouteDecision):
            raise TypeError(f"unexpected supervisor result {type(result).__name__}")
    except Exception as exc:
        # Nhãn ngoài schema là ValidationError, rơi vào đây cùng timeout/lỗi
        # mạng: mọi đường đều về "booking" (nhãn hoà của prompt).
        # asyncio.TimeoutError là Exception (3.11+). CancelledError (lượt mới
        # thay) KHÔNG bị nuốt: nó là BaseException.
        logger.warning("supervisor_failed", extra={"error": str(exc)[:200],
                                                   "error_type": type(exc).__name__})
        return {"route": "booking"}

    return {"route": result.route}

