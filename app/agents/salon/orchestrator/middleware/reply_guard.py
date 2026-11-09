"""Guard câu cuối của Orchestrator (spec §3.3 hàng "Guard câu cuối", §7, §8;
docs/architech-agent.md luật 4-5).

`ReplyGuardMiddleware.aafter_model` chỉ xét AIMessage KHÔNG có tool call —
đó chính là câu trả lời (cách `create_agent` kết thúc). Dữ kiện đối chiếu do
code dựng (`build_reply_facts`), không tin chữ model.

- Đạt → câu đã lọc tên tool / JSON (`strip_tool_text`) thay câu gốc; lời nhắc
  của lần viết lại (nếu có) bị xoá.
- Sai lần đầu → **xoá câu bị loại khỏi state** (model không được thấy lại câu
  sai của chính nó — bẫy 17), thêm lời nhắc tiếng Anh (mã + điều cần nói) và
  `jump_to: "model"`. Lần gọi đó quay lại `before_model` đầu tiên: vẫn qua nén
  và kiểm trần; `ContextMiddleware` ẩn tool cho lần viết lại.
- Sai lần hai → câu mẫu code, đóng phiên chờ mở trong lượt, `jump_to: "end"`.

Câu bị loại không được `ModelCallLimitMiddleware` đếm (`jump_to` bỏ qua các
`after_model` còn lại, gồm bộ đếm) — trần lệch tối đa 1, chấp nhận (spec §3.3).
"""
from __future__ import annotations

from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.salon.guard.checks import check_reply, strip_tool_text
from app.agents.salon.guard.facts import ReplyFacts, build_reply_facts
from app.agents.salon.guard.fallback import fallback_reply
from app.agents.salon.orchestrator.middleware.turns import (
    GUARD_HINT_MARK,
    current_question,
    is_guard_hint,
    previous_replies,
)
from app.agents.salon.orchestrator.state import (
    SalonContext,
    SalonState,
    read_facts,
    read_pending_outcome,
)
from app.agents.salon.shared.context import SnapshotHolder
from app.core.config import settings
from app.core.logging import get_logger
from app.services.waiting_session import WaitingSessionService

__all__ = ["GUARD_HINT_MARK", "ReplyGuardMiddleware", "guard_hint"]

logger = get_logger(__name__)

_CODE_HINTS = {
    "bad_tool_call": "Your last message had a tool call with invalid arguments.",
    "empty": "Your reply was empty. Write the reply to the customer in plain words; never "
             "write a tool call as text.",
    "claim": "Do not say anything was booked, moved, cancelled or held unless this turn's "
             "facts show it.",
    "unknown_time": "Mention only times that appear in the context block, this turn's facts or "
                    "the customer's message, copied exactly as their spoken value.",
    "unknown_price": "Mention only prices inside the price ranges from the service facts.",
    "missing_fact": "Mention each of these exactly as written: {must}.",
    "missing_question": "Ask the customer whether they agree to the new proposal.",
    "missing_phone": "Writing the appointment failed: give the shop phone number {phone}.",
    "phone_not_allowed": "Do not give the shop phone number; nothing failed to be written.",
    "pending_undecided": "A proposal is pending and was not decided this turn. Call "
                         "decide_pending first (label none if the customer talks about "
                         "something else), then reply.",
    "pronoun": 'Refer to yourself as "em" and address the customer exactly as the Address '
               "line says.",
    "repeat": "Do not reuse a sentence from your earlier replies.",
}


def guard_hint(codes: list[str], facts: ReplyFacts) -> str:
    """Lời nhắc tiếng Anh cho lần viết lại: mã + điều cần sửa. Không chép lại
    câu bị loại."""
    lines = ["Your last reply was not sent to the customer. Write a new reply using only "
             "facts you already have; do not look anything up again. Problems:"]
    for code in codes:
        hint = _CODE_HINTS.get(code, "Fix this problem.").format(
            must="; ".join(facts.must_mention), phone=settings.shop_phone or "")
        lines.append(f"- {code}: {hint}")
    return "\n".join(lines)


class ReplyGuardMiddleware(AgentMiddleware[SalonState, SalonContext]):
    def __init__(self, db: AsyncIOMotorDatabase, holder: SnapshotHolder) -> None:
        super().__init__()
        self.db = db
        self.holder = holder

    async def _facts(self, state: SalonState, context: SalonContext) -> ReplyFacts:
        messages = state["messages"]
        return await build_reply_facts(
            self.db, snapshot=await self.holder.current(), turn_facts=read_facts(state),
            turn_id=state.get("turn_id") or context.turn_id,
            turn_kind=state.get("turn_kind") or "typed",
            pending_outcome=read_pending_outcome(state),
            question=current_question(messages),
            previous_replies=previous_replies(messages),
            # Cờ của STATE, không suy từ ToolMessage: decide_pending bị từ chối /
            # bị veto vẫn để lại ToolMessage nhưng cờ vẫn False.
            pending_decided=bool(state.get("pending_decided")),
        )

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: SalonState, runtime: Runtime[SalonContext],
                           ) -> Optional[dict[str, Any]]:
        messages = state["messages"]
        reply = messages[-1] if messages else None
        if not isinstance(reply, AIMessage) or reply.tool_calls:
            return None

        context = runtime.context
        text = strip_tool_text(reply.text)
        facts = await self._facts(state, context)
        codes = check_reply(text, facts)
        if reply.invalid_tool_calls:
            # Tool call hỏng: `create_agent` coi là câu cuối (không có tool call
            # hợp lệ) — không bao giờ gửi khách, và xoá khỏi state cùng lúc.
            codes = ["bad_tool_call", *codes]
        hints = [RemoveMessage(id=m.id) for m in messages if is_guard_hint(m)]
        log_extra = {"user_id": context.user_id, "turn_id": context.turn_id,
                     "thread_id": context.thread_id, "codes": codes}

        if not codes:
            updates: list[Any] = list(hints)
            if text != reply.text:
                updates.append(reply.model_copy(update={"content": text}))
            return {"messages": updates} if updates else None

        if state.get("guard_rewrites", 0) == 0:
            logger.info("guard_rejected", extra=log_extra)
            return {
                "messages": [
                    RemoveMessage(id=reply.id),
                    HumanMessage(content=guard_hint(codes, facts),
                                 additional_kwargs={GUARD_HINT_MARK: codes}),
                ],
                "guard_rewrites": 1,
                "jump_to": "model",
            }

        closed = await WaitingSessionService(self.db).close_opened_in_turn(
            context.user_id, state.get("turn_id") or context.turn_id, reason="fallback")
        logger.warning("guard_violation", extra={**log_extra, "closed_sessions": closed})
        return {
            "messages": [
                RemoveMessage(id=reply.id), *hints,
                AIMessage(content=fallback_reply(facts), additional_kwargs={"salon_source": "code"}),
            ],
            "jump_to": "end",
        }
