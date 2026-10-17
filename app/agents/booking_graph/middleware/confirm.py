"""Bắt đề xuất từ kết quả tool vào state agent — không tool nào ghi lịch.

`propose_appointment` / `cancel_appointment` tạo đề xuất trong DB
(`ProposalService.create`) và trả JSON kèm `proposal_id`. Middleware này:
- lấy `proposal_id` ra, đưa vào state agent bằng `Command(update=...)` — TƯỜNG
  MINH, là giá trị trả về của bước tool, không phải closure ghi ngầm vào state
  (chốt cứng "Ghi Mongo");
- gỡ `proposal_id` khỏi JSON trước khi model đọc: đầu vào model y như A, và id
  là việc của code, model không cần (và không được) chép nó.

`make_agent_node` chuyển `proposal_id` ra graph ngoài; node `confirm` interrupt
trên id đó và chỉ `ProposalService.approve` mới ghi lịch. Đứng NGOÀI
`ToolErrorMiddleware` nên lỗi tool tới đây là câu tiếng Việt, không phải JSON
(F8) — khi đó không có đề xuất.
"""
import json
from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import NotRequired

# Tool tạo đề xuất và khoá dữ kiện chứng tỏ đề xuất thành công.
PROPOSAL_TOOLS = {"propose_appointment": "held", "cancel_appointment": "will_cancel"}


class ProposalState(AgentState):
    proposal_id: NotRequired[Optional[str]]


def _payload(name: str, content: Any) -> Optional[dict]:
    if name not in PROPOSAL_TOOLS or not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def proposal_id_from_tool(name: str, content: Any) -> Optional[str]:
    """`proposal_id` của một kết quả tool đề xuất thành công; None cho mọi thứ
    khác (tool khác, JSON `error`, câu lỗi không phải JSON, thiếu id)."""
    data = _payload(name, content)
    if not data or "error" in data or PROPOSAL_TOOLS[name] not in data:
        return None
    pid = data.get("proposal_id")
    return pid if isinstance(pid, str) and pid else None


def _without_proposal_id(content: str) -> str:
    data = json.loads(content)
    data.pop("proposal_id", None)
    return json.dumps(data, ensure_ascii=False)


class ConfirmMiddleware(AgentMiddleware):
    """Không đăng ký tool, không giữ gì trong `self` — mọi thứ đi qua state."""

    state_schema = ProposalState
    tools = []          # KHÔNG tool nào — càng không có tool ghi (bẫy #7)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ):
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        name = request.tool_call["name"]
        pid = proposal_id_from_tool(name, result.content)
        if pid is None:
            return result
        message = result.model_copy(update={"content": _without_proposal_id(result.content)})
        return Command(update={"messages": [message], "proposal_id": pid})
