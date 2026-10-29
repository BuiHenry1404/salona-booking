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

# `aside` ép về booking dù model định tuyến nơi khác (review live B3 #2):
# hỏi người nhà rảnh không / hẹn hỏi rồi nhắn lại đều là chuyện lịch khách.
FORCE_BOOKING_ASIDES = frozenset({"relative_free", "defer_only", "defer_with_request"})


def _route_from_analysis(analysis: dict, state: GraphState) -> str:
    """Áp luật định tuyến 1-4 (đã có `analysis`, dù tự gọi model hay dùng lại
    `prior`): hàng chờ > aside ép booking > trả lời câu hỏi booking > nhãn
    model (rơi về booking nếu lạ)."""
    if live_queue(state.get("queued")):
        # Luật 1 — đang đặt lần lượt (lỗi live B2 #2): còn lịch chờ đề xuất
        # thì mọi tin vào booking — "ừ" sau câu mời lịch kế không rơi vào social.
        logger.info("supervisor_queue_route")
        return "booking"
    if analysis.get("aside") in FORCE_BOOKING_ASIDES:
        # Luật 2 — lỗi live B3 #2.
        logger.info("supervisor_aside_route")
        return "booking"
    if state.get("last_route") == "booking" and analysis.get("answers_question"):
        # Luật 3 — lỗi LLM-02: agent hỏi "giữ 2 giờ chiều được không?" KHÔNG
        # qua `propose_appointment` (chưa có đề xuất) → lượt sau không "đang
        # chờ" (`waiting` ở `events.py`), vào thẳng supervisor từ START. Lượt
        # trước là booking + câu này trả lời câu hỏi đó → về booking.
        logger.info("supervisor_answer_route")
        return "booking"
    route = analysis.get("route")
    return route if route in VALID_ROUTES else "booking"


def _last_ai_text(messages) -> str:
    """Câu bot cuối TRƯỚC câu khách — câu khách đang trả lời (nếu có)."""
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            return str(message.content or "")
    return ""


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định qua bộ phân tích đầu lượt TurnAnalysis (Task 12b), rồi
    áp luật định tuyến 1-4 (`_route_from_analysis`) lên kết quả — kể cả nhánh
    hàng chờ: supervisor là chỗ DUY NHẤT gọi bộ phân tích cho tin mới (Task 1),
    node agent không còn tự gọi lại.

    Bộ phân tích KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp
    tới — chỉ vài lượt chat cuối.
    """
    prior = state.get("analysis")
    if prior:
        # Confirm đã phân tích CHÍNH câu này (đề xuất không còn duyệt được giữa
        # chừng → về supervisor): dùng lại, không gọi lần hai — mỗi lượt tối đa
        # một lời gọi bộ phân tích (Task 12b). Không trả lại "analysis" — đã
        # có sẵn trong state.
        logger.info("supervisor_reuse_analysis")
        return {"route": _route_from_analysis(prior, state)}

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
    # Hàng chờ (luật 1) vẫn gọi bộ phân tích — chỉ ép route ở
    # `_route_from_analysis` — để "analysis" luôn có trong state cho node
    # agent dùng, không phải tự gọi lại (lỗi 8).
    analysis = await analyzer.analyze_turn(
        recent, text, None, bot_question=_last_ai_text(earlier))
    analysis_dict = analysis.model_dump()
    return {"route": _route_from_analysis(analysis_dict, state),
            "analysis": analysis_dict}
