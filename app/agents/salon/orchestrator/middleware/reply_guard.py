"""Guard câu cuối của Orchestrator (spec guard rebuild, mục "Chính sách trong
ReplyGuardMiddleware"; docs/architech-agent.md luật 4-5).

`ReplyGuardMiddleware.aafter_model` chỉ xét AIMessage KHÔNG có tool call —
đó chính là câu trả lời (cách `create_agent` kết thúc). Câu trả lời là chữ
thường. Dữ kiện đối chiếu do code dựng (`reply_inputs_for` → `TurnResults` +
`ReplyPlan`), không tin chữ model; `validator.validate` kiểm chữ model.

- Đạt → câu báo kín của code + chữ model (đã qua `strip_tool_text`;
  `templates.with_taken` bỏ tiếng "Dạ" mở đầu chữ model khi có câu báo kín)
  thay câu gốc, và ghi `announced_turn_ids =
  plan.announce_turn_ids()`; lời nhắc của lần viết lại (nếu có) bị xoá.
- Sai lần đầu → **xoá câu bị loại khỏi state** (model không được thấy lại câu
  sai của chính nó — bẫy 17), thêm lời nhắc JSON tiếng Anh (mã, hành động
  còn thiếu, câu nhắc) và `jump_to: "model"`. Lần gọi đó quay lại
  `before_model` đầu tiên: vẫn qua nén và kiểm trần; `ContextMiddleware` ẩn
  tool cho lần viết lại.
- Sai lần hai → câu mẫu `templates.compose(plan)` (hoặc `GENERIC_FALLBACK` khi
  kế hoạch rỗng), ghi `announced_turn_ids`, đóng phiên chờ mở trong lượt trừ
  phiên câu mẫu hỏi (R12), `jump_to: "end"`.

Câu bị loại không được `ModelCallLimitMiddleware` đếm (`jump_to` bỏ qua các
`after_model` còn lại, gồm bộ đếm) — trần lệch tối đa 1, chấp nhận (spec §3.3).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.salon.guard.planner import ReplyPlan, build_plan
from app.agents.salon.guard.results import TurnResults, build_turn_results
from app.agents.salon.guard.templates import compose, with_taken
from app.agents.salon.guard.validator import Verdict, validate
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
from app.core.logging import get_logger
from app.services.waiting_session import WaitingSessionService

__all__ = ["GUARD_HINT_MARK", "HINTS", "ReplyGuardMiddleware", "guard_hint", "reply_inputs_for"]

logger = get_logger(__name__)

# Một câu nhắc tiếng Anh cho mỗi mã của `validator.CODES`.
HINTS = {
    "bad_tool_call": "Your last message had a tool call with invalid arguments.",
    "pending_undecided": "A proposal is pending and was not decided this turn. Call "
                         "decide_pending first (label none if the customer talks about "
                         "something else), then reply.",
    "plan_missing": "Tell every act and the ask in the Reply plan, with every time, price and "
                    "service name in it.",
    "unknown_time": "Mention only times from the context block.",
    "unknown_price": "Mention only prices inside this turn's price ranges.",
    "wrong_service": "Name only the services of this turn.",
    "taken_mentioned": "Do not mention the times in taken_told_by_code; the customer was "
                       "already told.",
    "machine_time": "Write times the way the reply plan writes them (for example 3 giờ chiều), "
                    "never as 15:00 or a date-time code.",
    "missing_question": "End with the question of the Reply plan: whether they agree to the "
                        "proposal, which offered time they choose, or the ask fields.",
    # `{phone}`: số tiệm của hành động `failed` (`plan.phone`).
    "missing_phone": "Give the shop phone number {phone} from the failed act.",
    "phone_not_allowed": "Do not give the shop phone number; nothing failed to be written.",
    "empty": "Your reply was empty. Write the reply to the customer in plain words; never "
             "write a tool call as text.",
    "foreign_script": "Write only Vietnamese letters.",
    "repeat": "Do not reuse a sentence from your earlier replies.",
}


def guard_hint(codes: list[str], verdict: Verdict, plan: ReplyPlan) -> str:
    """Lời nhắc JSON cho lần viết lại: mã bị loại, hành động còn thiếu (JSON
    như trong plan), câu nhắc ghép theo mã. Không chép lại câu bị loại."""
    hint = " ".join(HINTS[code].format(phone=plan.phone or "") for code in codes)
    return json.dumps({"rejected": codes, "missing": verdict.missing, "hint": hint},
                      ensure_ascii=False)


async def reply_inputs_for(db: AsyncIOMotorDatabase, holder: SnapshotHolder, state: SalonState,
                           context: Optional[SalonContext]) -> tuple[TurnResults, ReplyPlan]:
    """Dữ kiện + kế hoạch câu trả lời của lượt, dựng từ state và bối cảnh hiện
    tại — `ContextMiddleware` (khối bối cảnh) và guard dùng chung."""
    messages = state["messages"]
    results = await build_turn_results(
        db, snapshot=await holder.current(), turn_facts=read_facts(state),
        pending_outcome=read_pending_outcome(state),
        turn_id=state.get("turn_id") or getattr(context, "turn_id", None) or "",
        turn_kind=state.get("turn_kind") or "typed",
        question=current_question(messages),
        previous_replies=previous_replies(messages),
        # Cờ của STATE, không suy từ ToolMessage: decide_pending bị từ chối /
        # bị veto vẫn để lại ToolMessage nhưng cờ vẫn False.
        pending_decided=bool(state.get("pending_decided")),
    )
    return results, build_plan(results)


class ReplyGuardMiddleware(AgentMiddleware[SalonState, SalonContext]):
    def __init__(self, db: AsyncIOMotorDatabase, holder: SnapshotHolder) -> None:
        super().__init__()
        self.db = db
        self.holder = holder

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: SalonState, runtime: Runtime[SalonContext],
                           ) -> Optional[dict[str, Any]]:
        messages = state["messages"]
        reply = messages[-1] if messages else None
        if not isinstance(reply, AIMessage) or reply.tool_calls:
            return None

        context = runtime.context
        results, plan = await reply_inputs_for(self.db, self.holder, state, context)
        # Tool call hỏng: `create_agent` coi là câu cuối (không có tool call
        # hợp lệ) — không bao giờ gửi khách (`bad_tool_call`).
        verdict = validate(reply.text, plan, results,
                           invalid_tool_call=bool(reply.invalid_tool_calls))
        codes = list(verdict.codes)
        address = results.address
        hints = [RemoveMessage(id=m.id) for m in messages if is_guard_hint(m)]
        log_extra = {"user_id": context.user_id, "turn_id": context.turn_id,
                     "thread_id": context.thread_id, "codes": codes}

        if not codes:
            # Câu báo kín do code ghép SAU khi kiểm (validator không thấy nó).
            text = with_taken(plan, address, verdict.text)
            return {
                "messages": [*hints, reply.model_copy(update={"content": text})],
                "announced_turn_ids": plan.announce_turn_ids(),
            }

        # Câu của BOT bị loại (không phải câu khách) — thiếu nó thì không biết
        # guard chặn vì số / giờ nào (live run 1, 11/11).
        log_extra["rejected_reply"] = verdict.text or reply.text
        if state.get("guard_rewrites", 0) == 0:
            logger.info("guard_rejected", extra=log_extra)
            return {
                "messages": [
                    RemoveMessage(id=reply.id),
                    HumanMessage(content=guard_hint(codes, verdict, plan),
                                 additional_kwargs={GUARD_HINT_MARK: codes}),
                ],
                "guard_rewrites": 1,
                "jump_to": "model",
            }

        # Phiên mà câu mẫu hỏi câu hỏi của nó vẫn mở (Ruling R12).
        closed = await WaitingSessionService(self.db).close_opened_in_turn(
            context.user_id, state.get("turn_id") or context.turn_id, reason="fallback",
            keep=plan.asked_session)
        logger.warning("guard_violation", extra={**log_extra, "closed_sessions": closed})
        return {
            "messages": [
                RemoveMessage(id=reply.id), *hints,
                AIMessage(content=compose(plan, address), additional_kwargs={"salon_source": "code"}),
            ],
            "announced_turn_ids": plan.announce_turn_ids(),
            "jump_to": "end",
        }
