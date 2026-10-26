from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired


class GraphState(TypedDict, total=False):
    """State của một lượt chat. Mọi node đọc và ghi qua đây — không biến toàn cục,
    nên từng node kiểm thử độc lập được."""

    messages: Annotated[List[AnyMessage], add_messages]
    user_id: str
    context_block: str
    summary: List[str]
    slots: Optional[Any]         # ConversationSlots đã sạch; None khi không có
    proposal_id: Optional[str]       # đề xuất chờ khách đồng ý (DB `proposals`)
    queued: List[Dict[str, Any]]     # lịch khách xin thêm, chờ đề xuất lần lượt (context.queued_line)
    gate_next: str                   # confirm → "end" | "phrase" (agent booking + confirm_fact) | "booking" | "supervisor"
    asked_at: datetime                 # mốc câu khách (now_ms) — "bây giờ" của cửa sổ lịch sử
    history_after: Optional[datetime]  # covers_until của state nén; tin ≤ mốc này không gửi model
    route: str
    answer: str
    draft: str                       # câu thô của agent, trước guard (chỉ để soi)
    violations: List[str]            # GuardMiddleware ghi
    previous_replies: List[str]      # câu đáp LLM gần nhất (source == "llm")
    customer_text: str
    answer_source: str               # "llm" | "code" — ghi vào ChatMessage.source
    fallback: Optional[str]          # câu cứng của confirm
    confirm_fact: Optional[Dict[str, Any]]   # số liệu confirm vừa ghi → PhraseMiddleware
    # resume gõ chữ (mặc định) hay nút bấm UI (Task 5/6) — Task 3 đọc để viết
    # câu chốt bằng code thay vì qua agent.
    confirm_source: NotRequired[Literal["text", "button"]]
    # route của LƯỢT TRƯỚC (node `record` ghi). KHÔNG bị `fresh_turn_fields`
    # xoá mỗi lượt mới — phải sống qua checkpointer để supervisor lượt sau
    # biết "ừ" tiếp theo có phải đang nối lượt booking không (lỗi LLM-02).
    last_route: NotRequired[str]
    # Kết quả bộ phân tích đầu lượt (`TurnAnalysis.model_dump()`, Task 12b) —
    # supervisor hoặc confirm ghi, TỐI ĐA một lần mỗi lượt; `events` đọc sau
    # `complete` để ghi `stated`/`asked` vào slots. `fresh_turn_fields` đặt
    # lại None mỗi lượt: phân tích của lượt trước không được mang sang.
    analysis: NotRequired[Optional[dict]]
