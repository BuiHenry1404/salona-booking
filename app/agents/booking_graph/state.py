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
    pending_confirmation: Optional[Dict[str, Any]]
    route: str
    answer: str
    draft: str                       # câu LLM vừa viết, chưa qua guard
    original_draft: str              # draft trước rewrite — dùng khi rewrite vẫn hỏng
    violations: List[str]
    rewritten: bool
    previous_replies: List[str]      # câu đáp LLM gần nhất (source == "llm")
    customer_text: str
    answer_source: str               # "llm" | "code" — ghi vào ChatMessage.source
    fallback: Optional[str]          # câu cứng của confirm (Task 4)
    confirm_fact: Optional[Dict[str, Any]]   # số liệu confirm vừa ghi (Task 4)
    phrase_fact: Optional[Dict[str, Any]]
