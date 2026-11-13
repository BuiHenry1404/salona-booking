"""Guard câu cuối của Orchestrator (spec §3.3 hàng "Guard câu cuối", §7, §8;
docs/architech-agent.md luật 4-5).

`ReplyGuardMiddleware.aafter_model` chỉ xét AIMessage KHÔNG có tool call —
đó chính là câu trả lời (cách `create_agent` kết thúc). Câu trả lời là JSON các
đoạn trích dẫn (spec level C, `reply_format()`); `check_structured` parse →
ghép chữ → `strip_tool_text` → các luật. Dữ kiện đối chiếu do code dựng
(`build_reply_facts`), không tin chữ model.

- Đạt → chữ đã ghép thay JSON gốc (state / lịch sử / lượt sau chỉ thấy chữ
  thường) và ghi `announced_turn_ids` (quyết định / R0 được trích dẫn là đã
  báo); lời nhắc của lần viết lại (nếu có) bị xoá.
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

from app.agents.salon.guard.checks import check_structured
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

# Khoá `create_agent` (ProviderStrategy) ghi các đoạn đã parse của MỌI câu
# JSON. Guard đã đọc `reply.text` nên xoá nó ở cả ba nhánh: state / checkpoint
# chỉ giữ chữ đã ghép, và câu bị loại không bao giờ được lưu (chỉ log). Định
# tuyến đọc `jump_to` trước, câu không tool call vẫn kết thúc lượt — xoá khoá
# này không đổi luồng.
_STRUCTURED = "structured_response"

_CODE_HINTS = {
    "bad_tool_call": "Your last message had a tool call with invalid arguments.",
    "bad_structure": "Your reply was not the JSON list of segments. Answer only with segments; "
                     "ref and role are both null or both set.",
    "unknown_ref": "Use only ids from the Reply facts list.",
    "wrong_role": "Give each cited fact one of the roles listed for its id in Reply facts.",
    "ref_mismatch": "A cited segment says only its fact: the same clock time with an optional "
                    "day, a word of the service name, or an amount inside the price range.",
    "unreferenced": "Write every time, service name and price as its own segment with ref and "
                    "role.",
    "taken_not_told": "Tell the customer in its own sentence that each taken time is already "
                      "booked, and never invite them to choose it.",
    "empty": "Your reply was empty. Write the reply to the customer in plain words; never "
             "write a tool call as text.",
    "claim": "Do not say anything was booked, moved, cancelled or held unless this turn's "
             "facts show it.",
    "missing_fact": "Do what each line below says for the listed facts, citing each by its id.",
    "missing_question": "Ask the customer whether they agree to the new proposal.",
    "missing_phone": "Writing the appointment failed: give the shop phone number {phone}.",
    "phone_not_allowed": "Do not give the shop phone number; nothing failed to be written.",
    "pending_undecided": "A proposal is pending and was not decided this turn. Call "
                         "decide_pending first (label none if the customer talks about "
                         "something else), then reply.",
    "pronoun": 'Refer to yourself as "em" and address the customer exactly as the Address '
               "line says.",
    "foreign_script": "Write only Vietnamese letters.",
    "english": "Write the whole reply in Vietnamese.",
    "repeat": "Do not reuse a sentence from your earlier replies.",
}


def guard_hint(codes: list[str], facts: ReplyFacts, problems: list[str]) -> str:
    """Lời nhắc tiếng Anh cho lần viết lại: mã + điều cần sửa, rồi chi tiết
    từng lỗi (`problems` của `check_structured` — id, vai trò, dữ kiện bắt
    buộc). Không chép lại câu bị loại."""
    lines = ["Your last reply was not sent to the customer. Write a new reply using only "
             "facts you already have; do not look anything up again. Problems:"]
    for code in codes:
        hint = _CODE_HINTS.get(code, "Fix this problem.").format(phone=settings.shop_phone or "")
        lines.append(f"- {code}: {hint}")
    if problems:
        lines.append("Details:")
        lines += [f"- {problem}" for problem in problems]
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
        facts = await self._facts(state, context)
        result = check_structured(reply.text, facts)
        codes = list(result.codes)
        if reply.invalid_tool_calls:
            # Tool call hỏng: `create_agent` coi là câu cuối (không có tool call
            # hợp lệ) — không bao giờ gửi khách, và xoá khỏi state cùng lúc.
            # Nội dung của nó thường không phải JSON: `bad_tool_call` đã nói lý do.
            codes = ["bad_tool_call", *[c for c in result.codes if c != "bad_structure"]]
        hints = [RemoveMessage(id=m.id) for m in messages if is_guard_hint(m)]
        log_extra = {"user_id": context.user_id, "turn_id": context.turn_id,
                     "thread_id": context.thread_id, "codes": codes}

        if not codes:
            # Lưu CHỮ đã ghép, không lưu JSON: lịch sử, luật `repeat` và lượt
            # sau đọc chữ thường (spec level C §1).
            return {
                "messages": [*hints, reply.model_copy(update={"content": result.text})],
                "announced_turn_ids": list(result.announce_turn_ids),
                _STRUCTURED: None,
            }

        # Câu của BOT bị loại (không phải câu khách) — thiếu nó thì không biết
        # guard chặn vì số / giờ nào (live run 1, 11/11). `bad_structure`: chữ
        # thô model trả (vẫn là chữ của bot).
        log_extra["rejected_reply"] = result.text or reply.text
        if state.get("guard_rewrites", 0) == 0:
            logger.info("guard_rejected", extra=log_extra)
            return {
                "messages": [
                    RemoveMessage(id=reply.id),
                    HumanMessage(content=guard_hint(codes, facts, result.problems),
                                 additional_kwargs={GUARD_HINT_MARK: codes}),
                ],
                "guard_rewrites": 1,
                _STRUCTURED: None,
                "jump_to": "model",
            }

        # Phiên mà câu mẫu hỏi câu hỏi của nó vẫn mở (Ruling R12).
        closed = await WaitingSessionService(self.db).close_opened_in_turn(
            context.user_id, state.get("turn_id") or context.turn_id, reason="fallback",
            keep=facts.asked_session)
        logger.warning("guard_violation", extra={**log_extra, "closed_sessions": closed})
        return {
            "messages": [
                RemoveMessage(id=reply.id), *hints,
                AIMessage(content=fallback_reply(facts), additional_kwargs={"salon_source": "code"}),
            ],
            _STRUCTURED: None,
            "jump_to": "end",
        }
