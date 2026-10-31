"""Lưới an toàn dùng chung cho hai agent con (Appointment, Shop Info) — §8.1/§9.5
của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`.

- `allowlist(tools, names)`: code dựng agent cho một task CHỈ cấp đúng tool
  theo `action` (bảng §8.1/§8.2); tool không trong danh sách không tồn tại
  trong lượt đó — model không thể gọi nhầm.
- `OneProposalPerTaskMiddleware`: Appointment Agent chỉ được TẠO một đề xuất
  mỗi task con (C3). Đếm đề xuất đã tạo THÀNH CÔNG, không đếm số lần gọi —
  một lần `propose_appointment`/`cancel_appointment` thất bại (slot đã bị
  giữ, tham số sai...) không tạo gì cả nên không chặn lần thử lại.
"""
import json
from typing import Any, Awaitable, Callable, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

PROPOSAL_TOOLS = ("propose_appointment", "cancel_appointment")


def allowlist(tools: Sequence[BaseTool], names: Sequence[str]) -> list[BaseTool]:
    """Giữ đúng thứ tự của `names`. Tên không có trong `tools` là lỗi lập
    trình (bảng allowlist trong code sai action) → `ValueError`, không âm thầm bỏ qua."""
    by_name = {t.name: t for t in tools}
    result = []
    for name in names:
        if name not in by_name:
            raise ValueError(f"Tool '{name}' không có trong danh sách tool truyền vào allowlist().")
        result.append(by_name[name])
    return result


def _was_proposal_created(message: ToolMessage) -> bool:
    """Đề xuất coi là đã TẠO khi content là JSON có khoá `proposal_id`.
    Content không phải JSON, hoặc JSON không có `proposal_id` (vd. lỗi
    `error_code`), coi là chưa tạo gì."""
    try:
        data = json.loads(message.content)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and "proposal_id" in data


def _proposals_created_this_task(messages) -> int:
    """Số đề xuất đã tạo thành công kể từ HumanMessage cuối (cùng cách đếm
    `_rounds_this_turn` trong `app/agents/booking_graph/middleware/tools.py`)."""
    count = 0
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if (isinstance(m, ToolMessage) and m.name in PROPOSAL_TOOLS
                and _was_proposal_created(m)):
            count += 1
    return count


class OneProposalPerTaskMiddleware(AgentMiddleware):
    """Chặn lần tạo đề xuất thứ hai trong cùng một task con (C3)."""

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ):
        call = request.tool_call
        if call["name"] not in PROPOSAL_TOOLS:
            return await handler(request)

        messages = request.state["messages"]
        if _proposals_created_this_task(messages) >= 1:
            return ToolMessage(
                content='{"error_code":"one_proposal_per_task"}',
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        return await handler(request)
