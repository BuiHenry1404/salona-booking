"""Guard của agent — `after_agent`, chạy SAU CÙNG trước khi agent trả kết quả.

Thay hai node `guard` ⇄ `rewrite` cũ của graph ngoài, cùng luật, cùng thứ tự
(`guard.apply_guard` là bản DUY NHẤT của luật — CONTEXT.md bẫy #21):
- câu cuối của agent (AIMessage cuối; rỗng → FALLBACK_ANSWER như node cũ) là
  draft, xoá tên tool trước mọi phép kiểm;
- câu chốt lịch (`confirm_fact`/`phrase_fact`): sai số liệu → câu cứng;
- câu thường: vi phạm → rewrite MỘT lần (tag `rewrite`, không stream); vẫn
  hỏng → draft GỐC, trừ ca chỉ lỗi `repeat`.

Trả `answer` / `answer_source` / `violations` vào state agent; `make_agent_node`
chuyển chúng ra graph ngoài, `record` ghi `answer` vào thread.

`self.user` là cấu hình (graph dựng lại mỗi lượt cho đúng một khách), không
phải trạng thái lượt chat.
"""
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from typing_extensions import NotRequired

from app.agents.booking_graph import rewrite
from app.agents.booking_graph.guard import apply_guard
from app.agents.booking_graph.middleware.tools import FALLBACK_ANSWER
from app.models.user import User


class GuardState(AgentState):
    answer: NotRequired[str]
    answer_source: NotRequired[str]
    violations: NotRequired[List[str]]
    previous_replies: NotRequired[List[str]]
    customer_text: NotRequired[str]
    confirm_fact: NotRequired[Optional[Dict[str, Any]]]
    phrase_fact: NotRequired[Optional[Dict[str, Any]]]
    fallback: NotRequired[Optional[str]]
    context_block: NotRequired[str]


def _last_ai_text(messages) -> str:
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            return str(message.content or "")
    return ""


class GuardMiddleware(AgentMiddleware):
    state_schema = GuardState
    tools = []

    def __init__(self, user: User):
        super().__init__()
        self.user = user

    async def aafter_agent(self, state, runtime) -> Dict[str, Any]:
        draft = _last_ai_text(state.get("messages")) or FALLBACK_ANSWER
        # Gọi qua module (không import tên): test thay `rewrite.rewrite_once`.
        return await apply_guard({**state, "draft": draft}, self.user, rewrite.rewrite_once)
