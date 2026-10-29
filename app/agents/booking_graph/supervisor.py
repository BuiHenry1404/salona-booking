from typing import Literal

from langchain_core.messages import AIMessage

from app.agents.booking_graph import analyzer
from app.agents.booking_graph.context import live_queue
from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.state import GraphState
from app.core.logging import get_logger

logger = get_logger(__name__)

Route = Literal["booking", "shop", "social"]
VALID_ROUTES = set(Route.__args__)
# Số tin cuối của cửa sổ lịch sử đưa cho bộ phân tích — TÍNH CẢ câu khách
# (như trước, khi supervisor tự gọi model): câu khách đi riêng trong
# HumanMessage cuối do `analyze_turn` dựng, nên phần lịch sử là 3 tin trước.
SUPERVISOR_HISTORY_TURNS = 4

# Ngân sách thời gian (review M4 I-1, lỗi live 1) giờ là của bộ phân tích:
# `analyzer.ANALYZE_TIMEOUT_SECONDS` (8 giây bọc CẢ bước, 1 lần thử lại SDK).
# Treo/lỗi → `TurnAnalysis.fallback` → route "booking" (nhãn hoà).


def _last_ai_text(messages) -> str:
    """Câu bot cuối TRƯỚC câu khách — câu khách đang trả lời (nếu có)."""
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            return str(message.content or "")
    return ""


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định qua bộ phân tích đầu lượt TurnAnalysis (Task 12b).

    Bộ phân tích KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp
    tới — chỉ vài lượt chat cuối. Hàng chờ đặt lần lượt giữ lối tắt code về
    booking.
    """
    if live_queue(state.get("queued")):
        # Đang đặt lần lượt (lỗi live B2 #2): còn lịch chờ đề xuất thì mọi tin
        # vào booking — "ừ" sau câu mời lịch kế không được rơi vào social.
        logger.info("supervisor_queue_route")
        return {"route": "booking"}

    prior = state.get("analysis")
    if prior:
        # Confirm đã phân tích CHÍNH câu này (đề xuất không còn duyệt được giữa
        # chừng → về supervisor): dùng lại, không gọi lần hai — mỗi lượt tối đa
        # một lời gọi bộ phân tích (Task 12b).
        route = prior.get("route")
        logger.info("supervisor_reuse_analysis")
        return {"route": route if route in VALID_ROUTES else "booking"}

    text = str(getattr(state["messages"][-1], "content", "") or "")

    # Vài lượt cuối của CỬA SỔ lịch sử (như A), không phải của cả thread:
    # tin đã nén hay của hôm qua không được kéo định tuyến.
    earlier = state["messages"][:-1]
    window = model_history(earlier, after=state.get("history_after"),
                           now=state.get("asked_at"))
    recent = window[-(SUPERVISOR_HISTORY_TURNS - 1):]

    # Một lượt structured output (Task 12b) thay lượt định tuyến cũ: cùng lúc
    # ra route VÀ dữ kiện cho slots. Tag `analyze`, streaming=False (bẫy #8),
    # mọi lỗi/timeout → fallback "booking" — `analyze_turn` không bao giờ ném.
    # Gọi qua module (`analyzer.analyze_turn`) để test thay được một chỗ.
    analysis = await analyzer.analyze_turn(
        recent, text, None, bot_question=_last_ai_text(earlier))
    route = analysis.route if analysis.route in VALID_ROUTES else "booking"
    return {"route": route, "analysis": analysis.model_dump()}
