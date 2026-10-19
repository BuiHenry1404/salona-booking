from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    """State của một lượt chat. Mọi node đọc và ghi qua đây — không biến toàn cục,
    nên từng node kiểm thử độc lập được."""

    messages: Annotated[List[AnyMessage], add_messages]
    user_id: str
    context_block: str
    summary: List[str]
    slots: Optional[Any]         # ConversationSlots đã sạch; None khi không có
    proposal_id: Optional[str]       # đề xuất chờ khách đồng ý (DB `proposals`)
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
