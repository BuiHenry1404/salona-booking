"""Đầu lượt của Orchestrator (spec §3.3 hàng "Đầu lượt", §3.4).

`TurnStartMiddleware.abefore_agent` chạy MỘT lần mỗi lượt, trước mọi lần gọi model:

1. **Sửa thread theo cấu trúc**, ở bất kỳ vị trí nào: lượt bị cắt (timeout,
   huỷ vì lượt mới, server khởi động lại) có thể để lại AIMessage có tool call
   mà thiếu ToolMessage — Azure trả 400 cho mọi yêu cầu sau đó, khách kẹt cả
   ngày (bẫy 13). Xoá AIMessage đó cùng các ToolMessage của nó; xoá luôn
   ToolMessage lẻ không còn tool call nào và lời nhắc guard còn sót. Câu khách
   của lượt dở giữ lại.
2. **Đặt lại khoá theo lượt** (`turn_facts`, bộ đếm, cờ quyết định...). Lượt
   nút bấm (`context.preset`) đặt quyết định của nút NGAY SAU bước đặt lại, để
   bối cảnh lẫn guard thấy nó.

Không đụng phiên chờ: chúng ở Mongo, `WaitingSessionService.active` tự bỏ
phiên hết hạn lúc đọc.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from app.agents.salon.orchestrator.middleware.turns import call_ids, has_calls, is_guard_hint
from app.agents.salon.orchestrator.state import RESET, SalonContext, SalonState
from app.agents.salon.shared.schemas import DecisionFact
from app.core.logging import get_logger

logger = get_logger(__name__)


def broken_message_ids(messages: list[BaseMessage]) -> list[str]:
    """Id các message phải xoá để thread hợp lệ lại, theo thứ tự trong thread."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    drop: set[str] = set()
    dropped_calls: set[str] = set()
    live_calls: set[str] = set()
    for message in messages:
        if not has_calls(message):
            continue
        # Tính cả `invalid_tool_calls` — câu trả lời kèm tool call hỏng mà
        # không có ToolMessage cũng làm Azure trả 400 (review Task 13).
        ids = call_ids(message)
        if ids - answered:
            drop.add(message.id)
            dropped_calls |= ids
        else:
            live_calls |= ids
    for message in messages:
        if isinstance(message, ToolMessage) and (
                message.tool_call_id in dropped_calls or message.tool_call_id not in live_calls):
            drop.add(message.id)
        elif is_guard_hint(message):
            drop.add(message.id)
    # Chỉ id CÓ trong thread — xoá id lạ là lỗi trong `add_messages`.
    return [m.id for m in messages if m.id is not None and m.id in drop]


class TurnStartMiddleware(AgentMiddleware[SalonState, SalonContext]):
    """Sửa thread và đặt lại state theo lượt, ở đầu mỗi lượt."""

    async def abefore_agent(self, state: SalonState, runtime: Runtime[SalonContext],
                            ) -> Optional[dict[str, Any]]:
        context = runtime.context
        preset = context.preset
        broken = broken_message_ids(state["messages"])
        if broken:
            logger.info("thread_repaired", extra={
                "user_id": context.user_id, "turn_id": context.turn_id,
                "thread_id": context.thread_id, "removed": len(broken),
            })

        turn_facts: Any = RESET
        if preset is not None:
            decision = DecisionFact(outcome=preset.outcome, proposal_kind=preset.kind,
                                    at=preset.at, error=preset.error)
            turn_facts = [RESET, decision.model_dump(mode="json")]

        update: dict[str, Any] = {
            "turn_facts": turn_facts,
            "failed_calls": RESET,
            "proposals_created": RESET,
            "decide_calls": 0,
            "guard_rewrites": 0,
            "turn_id": context.turn_id,
            "turn_kind": "button" if preset is not None else "typed",
            "pending_decided": preset is not None,
            "pending_outcome": preset.model_dump(mode="json") if preset is not None else None,
        }
        if broken:
            update["messages"] = [RemoveMessage(id=message_id) for message_id in broken]
        return update
