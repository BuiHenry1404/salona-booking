from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    """State của một lượt chat. Mọi node đọc và ghi qua đây — không biến toàn cục,
    nên từng node kiểm thử độc lập được."""

    messages: Annotated[List[AnyMessage], add_messages]
    user_id: str
    context_block: str
    digest: List[str]
    pending_confirmation: Optional[Dict[str, Any]]
    route: str
    answer: str
