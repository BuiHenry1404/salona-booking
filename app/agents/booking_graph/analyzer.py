"""Bộ phân tích đầu lượt (spec mục 3, sửa 25/10/2026).

Thay LƯỢT của supervisor (định tuyến) VÀ của `reply.classify_reply` (đọc câu
đáp đề xuất): một lượt structured output duy nhất, mỗi turn vẫn tối đa một
lượt LLM trước agent — không thêm lượt nào. Task 12b nối nó vào graph:
`supervisor.supervise` (tin mới, sau lối tắt code) và nhãn cuối của
`reply.decide` trong node `confirm` (câu gõ chữ, sau lưới phủ quyết/go-ahead).

Mọi kết quả — kể cả bản lùi an toàn khi lỗi/timeout — đều phải qua
`sanitize_analysis` trước khi dùng: model có thể bịa `time_text` không phải
câu khách gõ, gắn nhãn `self_address` sai giới, hay nhét câu mệnh lệnh vào
`stated`. Lưới code không tin model, giống `sanitize_slots`.
"""
import asyncio
from typing import Any, List, Literal, Mapping, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.booking_graph.prompts import ANALYZE_PROMPT
from app.agents.booking_graph.reply import _describe as _describe_proposal
from app.agents.llm import build_chat_model
from app.core.logging import get_logger
from app.core.text import detect_self_address, strip_diacritics
from app.services.conversation_state import clean_free_text_item

logger = get_logger(__name__)

# Như các bộ phân loại khác (bẫy #10): trần ngắn hơn Azure thật mất 2-3 giây
# sẽ khiến nhánh model không bao giờ chạy, mọi lượt rơi vào fallback.
ANALYZE_TIMEOUT_SECONDS = 8
ANALYZE_MAX_RETRIES = 1

Route = Literal["booking", "shop", "social"]
ReplyLabel = Literal["approve", "reject", "change", "unclear"]
SelfAddress = Literal["anh", "chị"]


class TurnAnalysis(BaseModel):
    """Kết quả bộ phân tích. Mọi trường trừ `route` đều tuỳ chọn/rỗng —
    "không nói gì" là câu trả lời đúng cho phần lớn lượt tán gẫu."""

    route: Route = "booking"
    reply_to_proposal: Optional[ReplyLabel] = None
    service: Optional[str] = None
    time_text: Optional[str] = None
    self_address: Optional[SelfAddress] = None
    stated: List[str] = Field(default_factory=list)
    answers_question: bool = False

    @classmethod
    def fallback(cls, has_pending: bool) -> "TurnAnalysis":
        """Bản lùi an toàn khi model lỗi/timeout/sai schema. `route="booking"`
        là nhãn hoà (như supervisor); `reply_to_proposal="unclear"` CHỈ khi có
        đề xuất đang chờ — không có đề xuất thì nhãn này vô nghĩa."""
        return cls(route="booking",
                   reply_to_proposal="unclear" if has_pending else None)


def _describe_pending(pending: Optional[Mapping[str, Any]]) -> str:
    """Mô tả đề xuất cho model — CHỈ giờ, không ghi chú khách gõ (cùng luật
    với `reply._describe`: ghi chú là chữ khách, có thể tiêm lệnh)."""
    if not pending:
        return "(no pending proposal)"
    return _describe_proposal(pending)


async def analyze_turn(messages: Sequence[BaseMessage], customer_text: str,
                       pending: Optional[Mapping[str, Any]],
                       bot_question: str = "") -> TurnAnalysis:
    """Một lượt structured output, tag `analyze`, `streaming=False` (bẫy #8:
    JSON phân tích không bao giờ được stream ra màn hình khách). Mọi lỗi/
    timeout/kết quả sai schema → `TurnAnalysis.fallback`. Kết quả LUÔN qua
    `sanitize_analysis` trước khi trả về.

    Chữ khách gõ (và câu bot vừa hỏi) nằm trong `HumanMessage` CUỐI, không
    phải `SystemMessage` (review Task 12 #4) — cùng cấu trúc với
    `reply.classify_reply`: vai "system" là chỗ của chỉ dẫn cố định, không
    phải chữ khách tự gõ (chữ khách trong vai system dễ bị model đọc như một
    chỉ dẫn thay vì dữ liệu cần phân tích).
    """
    has_pending = pending is not None
    try:
        model = build_chat_model(tags=["analyze"], streaming=False,
                                 reasoning_effort="low",
                                 timeout=ANALYZE_TIMEOUT_SECONDS,
                                 max_retries=ANALYZE_MAX_RETRIES)
        structured = model.with_structured_output(TurnAnalysis)
        human = HumanMessage(content=(
            f"{_describe_pending(pending)}\n"
            f"Receptionist's last message: {bot_question or '(none)'}\n"
            f"Customer's new message: {customer_text}"))
        result = await asyncio.wait_for(
            structured.ainvoke([SystemMessage(content=ANALYZE_PROMPT), *messages, human]),
            timeout=ANALYZE_TIMEOUT_SECONDS)
        if not isinstance(result, TurnAnalysis):
            raise TypeError(f"unexpected analyzer result {type(result).__name__}")
    except Exception as exc:
        logger.warning("analyze_failed", extra={"error": str(exc)[:200],
                                                "error_type": type(exc).__name__})
        result = TurnAnalysis.fallback(has_pending)
    return sanitize_analysis(result, customer_text, has_pending)


def sanitize_analysis(a: TurnAnalysis, customer_text: str, has_pending: bool) -> TurnAnalysis:
    """Lọc kết quả bộ phân tích — KHÔNG tin model, giống `sanitize_slots`.

    - `reply_to_proposal`: None khi không có đề xuất đang chờ (nhãn vô nghĩa);
      có đề xuất mà model trả None/thiếu thì ép "unclear" (không bao giờ tự
      suy ra approve).
    - `time_text`: phải là CHUỖI CON của câu khách (bỏ dấu, hạ chữ thường) —
      model không được bịa cụm giờ khách chưa từng nói.
    - `self_address`: chỉ giữ khi khớp với `detect_self_address` (regex) trên
      chính câu khách — model không được tự suy diễn giới tính.
    - `service`/`stated`: qua cùng bộ lọc với `sanitize_slots`
      (≤60 ký tự, bỏ SĐT/địa chỉ/câu mệnh lệnh); `stated` tối đa 2 mục.
    """
    reply_to_proposal: Optional[ReplyLabel]
    if not has_pending:
        reply_to_proposal = None
    else:
        reply_to_proposal = a.reply_to_proposal or "unclear"

    time_text = a.time_text
    if time_text:
        haystack = strip_diacritics((customer_text or "").lower())
        needle = strip_diacritics(time_text.lower())
        if not needle or needle not in haystack:
            time_text = None

    self_address = a.self_address
    if self_address is not None and self_address != detect_self_address(customer_text):
        self_address = None

    service = clean_free_text_item(a.service)

    stated: List[str] = []
    for item in a.stated or []:
        cleaned = clean_free_text_item(item)
        if cleaned and cleaned not in stated:
            stated.append(cleaned)
    stated = stated[:2]

    return TurnAnalysis(
        route=a.route if a.route in ("booking", "shop", "social") else "booking",
        reply_to_proposal=reply_to_proposal,
        service=service,
        time_text=time_text,
        self_address=self_address,
        stated=stated,
        answers_question=bool(a.answers_question),
    )
