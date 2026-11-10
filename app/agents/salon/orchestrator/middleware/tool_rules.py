"""Luật kiểm tool call của Orchestrator (Task 11, spec §3.3 hàng "Kiểm tool
call", §4.1, §8; docs/architech-agent.md luật 3, 4, 6).

`ToolRulesMiddleware.awrap_tool_call` chạy TRƯỚC mỗi tool điều khiển
(`CONTROL_TOOLS`). Vi phạm → trả `ToolMessage(status="error",
content=JSON{error_code, hint})`, KHÔNG gọi handler (tool không chạy, DB không
đổi); model đọc mã + gợi ý rồi tự sửa ở vòng sau. Ẩn tool theo trạng thái ở
từng lần gọi model là việc của ContextMiddleware (Task 13) — luật ở đây là
lưới an toàn của nó, không thay nó.

Thứ tự kiểm (dừng ở luật đầu tiên trượt):

1. `bad_args` — tham số sai schema (kiểm lại ở đây vì các luật sau đọc tham số).
1b. `unknown_service` — `service_ids` ngoài các id code đã biết.
2. `no_session` — `continue_session=true` mà agent đó không có phiên còn hạn.
3. `must_decide_pending` — `appointment_task` khi đề xuất chờ chưa quyết.
4. `no_pending` / `already_decided` — `decide_pending` sai lúc.
5. `one_proposal_per_turn` — `appointment_task` khi lượt đã có đề xuất mới.
6. `retry_exhausted` — gọi lại agent đã `failed` hai lần trong lượt.
7. `same_agent_twice_in_round` — hai call cùng agent con trong một vòng.
8. `service_unclear` — `appointment_task` khi dịch vụ khách nhắc còn chưa rõ:
   phiên Dịch vụ đang chờ, hoặc lần gọi agent Dịch vụ MỚI NHẤT của lượt trả
   `need_info` / `failed` (spec §6.2: hỏi rõ trước, không đề xuất).
8b. `service_required` — cuộc trò chuyện đã có dịch vụ (lượt trước hoặc lượt
   này) mà `appointment_task` không kèm `service_ids` (để lịch nhuộm không bị
   đặt 30 phút, spec §4.1).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.salon.orchestrator.state import SalonState
from app.agents.salon.shared.context import SnapshotHolder, TurnSnapshot
# Từ `shared/`, không qua `orchestrator/tools.py`: tools.py import
# `middleware/budget.py`, import ngược lại sẽ thành vòng.
from app.agents.salon.shared.tool_names import CONTROL_TOOLS
from app.core.logging import get_logger

logger = get_logger(__name__)

# Tool giao việc → tên agent con (khoá phiên chờ, `failed_calls`).
_TASK_AGENTS = {
    "appointment_task": "appointment",
    "shop_info_task": "shop_info",
    "service_task": "service",
}

_HINTS = {
    "bad_args": "Arguments do not match the tool schema. Send every parameter with the right "
                "type (use null for an empty optional value).",
    "unknown_service": "service_ids may only contain service ids already identified in this "
                       "conversation. Pass null or ask the service agent first.",
    "no_session": "This agent has no waiting session (none, or it expired). Call it with "
                  "continue_session=false as a new task.",
    "must_decide_pending": "A proposal is pending. Call decide_pending first, then the "
                           "appointment task if still needed.",
    "no_pending": "There is no pending proposal to decide.",
    "already_decided": "The pending proposal was already decided in this turn. Do not call "
                       "decide_pending again.",
    "one_proposal_per_turn": "A new proposal was already created this turn. Answer the "
                             "customer and let them confirm it first.",
    "retry_exhausted": "This agent already failed twice this turn. Do not call it again; "
                       "answer the customer with what is known.",
    "same_agent_twice_in_round": "Only one call per agent in the same step. Put everything "
                                 "into one request.",
    "service_unclear": "The service the customer named is not clear yet. Ask the customer the "
                       "service agent's question first; do not book until the service agent "
                       "returns ok.",
    "service_required": "This conversation already identified services. Pass their ids as "
                        "service_ids, or call service_task to settle which service this "
                        "appointment is for.",
}

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


def _refuse(request: ToolCallRequest, code: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"error_code": code, "hint": _HINTS[code]}),
        name=request.tool_call["name"], tool_call_id=request.tool_call["id"], status="error",
    )


def _bad_args(request: ToolCallRequest) -> bool:
    """Kiểm lại tham số theo schema strict của tool: đúng kiểu (không ép
    "yes" thành `True`), đủ khoá, không khoá lạ."""
    schema = request.tool.tool_call_schema
    args = request.tool_call.get("args")
    if not isinstance(args, dict) or set(args) - set(schema.model_fields):
        return True
    try:
        schema.model_validate(args, strict=True)
    except ValidationError:
        return True
    return False


def _service_choices(snapshot: TurnSnapshot, state: dict) -> set[str]:
    """Id dịch vụ code đã biết (spec §4.1): `ServiceFact` lượt này ∪ phiên
    Dịch vụ đang chờ ∪ đề xuất chờ ∪ dịch vụ đã nhận ra ở lượt trước."""
    choices = {fact["service_id"] for fact in state.get("turn_facts", [])
               if fact.get("kind") == "service"}
    service_session = snapshot.sessions.get("service")
    if service_session is not None:
        choices.update(service_session.memory.get("service_ids", []))
    if snapshot.pending is not None:
        choices.update(snapshot.pending.service_ids)
    choices.update(fact.service_id for fact in snapshot.known_services)
    return choices


def _service_unclear(snapshot: TurnSnapshot, state: dict) -> bool:
    """Phiên Dịch vụ đang chờ, hoặc lần gọi agent Dịch vụ MỚI NHẤT của lượt
    chưa chốt được dịch vụ (gọi lại thành công sau đó thì thôi chặn)."""
    if "service" in snapshot.sessions:
        return True
    statuses = [fact.get("status") for fact in state.get("turn_facts", [])
                if fact.get("kind") == "agent_status" and fact.get("agent") == "service"]
    return bool(statuses) and statuses[-1] in ("need_info", "failed")


def _has_services(snapshot: TurnSnapshot, state: dict) -> bool:
    """Cuộc trò chuyện đã có dịch vụ: nhận ra ở lượt trước hoặc trong lượt này."""
    return bool(snapshot.known_services) or any(
        fact.get("kind") == "service" for fact in state.get("turn_facts", []))


def _earlier_in_round(request: ToolCallRequest, state: dict) -> bool:
    """Trong AIMessage phát ra call này, có call CÙNG TÊN đứng trước nó không.
    Các call cùng vòng chạy song song trên cùng một state, nên luật đếm theo
    state (đề xuất, quyết định) không thấy nhau — phải nhìn chính AIMessage."""
    call_id = request.tool_call["id"]
    name = request.tool_call["name"]
    for message in reversed(state.get("messages", [])):
        if not isinstance(message, AIMessage):
            continue
        ids = [call["id"] for call in message.tool_calls]
        if call_id not in ids:
            continue
        earlier = message.tool_calls[:ids.index(call_id)]
        return any(call["name"] == name for call in earlier)
    return False


class ToolRulesMiddleware(AgentMiddleware[SalonState, Any]):
    """Lưới an toàn bằng code cho mọi tool call của Orchestrator."""

    def __init__(self, holder: SnapshotHolder) -> None:
        super().__init__()
        self.holder = holder

    async def _violation(self, request: ToolCallRequest) -> Optional[str]:
        name = request.tool_call["name"]
        args = request.tool_call["args"]
        state = request.state
        if _bad_args(request):
            return "bad_args"
        snapshot = await self.holder.current()

        service_ids = args.get("service_ids") or []
        if service_ids and not set(service_ids) <= _service_choices(snapshot, state):
            return "unknown_service"

        agent = _TASK_AGENTS.get(name)
        if agent is not None and args.get("continue_session") and agent not in snapshot.sessions:
            return "no_session"

        pending_decided = bool(state.get("pending_decided"))
        if name == "appointment_task" and snapshot.pending is not None and not pending_decided:
            return "must_decide_pending"

        if name == "decide_pending":
            if pending_decided or _earlier_in_round(request, state):
                return "already_decided"
            if snapshot.pending is None:
                return "no_pending"
            return None

        if name == "appointment_task" and state.get("proposals_created", 0) >= 1:
            return "one_proposal_per_turn"
        if state.get("failed_calls", []).count(agent) >= 2:
            return "retry_exhausted"
        if _earlier_in_round(request, state):
            return "same_agent_twice_in_round"
        if name == "appointment_task":
            if _service_unclear(snapshot, state):
                return "service_unclear"
            if not service_ids and _has_services(snapshot, state):
                return "service_required"
        return None

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Handler,
                              ) -> ToolMessage | Command:
        # Tên tool lạ: ToolNode hoãn kiểm tên tới lúc chạy — để nó tự trả lỗi.
        if request.tool is None or request.tool_call["name"] not in CONTROL_TOOLS:
            return await handler(request)
        code = await self._violation(request)
        if code is None:
            return await handler(request)
        context = request.runtime.context
        logger.info("tool_call_refused", extra={
            "user_id": context.user_id, "turn_id": context.turn_id,
            "thread_id": context.thread_id, "tool": request.tool_call["name"],
            "error_code": code,
        })
        return _refuse(request, code)
