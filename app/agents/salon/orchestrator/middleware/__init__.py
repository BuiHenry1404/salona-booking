"""Middleware của Orchestrator (spec §3.3) và chồng đúng thứ tự của nó.

`orchestrator_middleware` trả danh sách theo thứ tự ngoài → trong. Ngữ nghĩa
thứ tự (langchain 1.4.6): `before_*` chạy đầu → cuối, `after_model` chạy cuối
→ đầu, `wrap_*` lồng nhau (đầu danh sách là lớp ngoài cùng); `jump_to: "model"`
quay về `before_model` đầu tiên nên lần viết lại của guard cũng qua nén và
kiểm trần.

`ModelRetryMiddleware(on_failure="error")` BẮT BUỘC: mặc định "continue" trả
một AIMessage tiếng Anh chứa lỗi, guard sẽ coi đó là câu trả lời. Trần dùng
`exit_behavior="error"`: "end" tự chèn AIMessage tiếng Anh và nhảy END từ
`before_model`, guard không thấy.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.llm import build_chat_model
from app.agents.salon.memory.prompts import SUMMARY_PROMPT
from app.agents.salon.orchestrator.middleware.budget import BudgetMiddleware, LlmBudgetExceeded
from app.agents.salon.orchestrator.middleware.context import ContextMiddleware
from app.agents.salon.orchestrator.middleware.reply_guard import ReplyGuardMiddleware
from app.agents.salon.orchestrator.middleware.tool_rules import ToolRulesMiddleware
from app.agents.salon.orchestrator.middleware.turn_start import TurnStartMiddleware
from app.agents.salon.orchestrator.middleware.turns import GUARD_HINT_MARK
from app.agents.salon.shared.context import SnapshotHolder
from app.agents.salon.subagents.dispatch import MODEL_RETRY_DELAY_SECONDS, is_transient
from app.core.config import settings
from app.models.user import User

__all__ = [
    "BudgetMiddleware", "ContextMiddleware", "GUARD_HINT_MARK", "LlmBudgetExceeded",
    "ReplyGuardMiddleware", "ToolRulesMiddleware", "TurnStartMiddleware",
    "orchestrator_middleware",
]


def orchestrator_middleware(db: AsyncIOMotorDatabase, user: User, holder: SnapshotHolder,
                            store: Any) -> list[AgentMiddleware]:
    """Chồng middleware của Orchestrator cho MỘT lượt, đúng thứ tự spec §3.3."""
    return [
        TurnStartMiddleware(),
        # Model nén nhỏ, tag riêng, không stream. Lời gọi này đi thẳng, không qua
        # `wrap_model_call`, nên BudgetMiddleware không đếm — chấp nhận.
        SummarizationMiddleware(
            model=build_chat_model(tags=["summary"], streaming=False, max_retries=0),
            trigger=("tokens", settings.salon_summary_trigger_tokens),
            keep=("messages", settings.salon_summary_keep_messages),
            summary_prompt=SUMMARY_PROMPT,
        ),
        ContextMiddleware(holder, store),
        ToolRulesMiddleware(holder),
        ModelCallLimitMiddleware(run_limit=settings.salon_model_calls_per_turn,
                                 exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=settings.salon_tool_calls_per_turn,
                                exit_behavior="error"),
        ReplyGuardMiddleware(db, holder),
        ModelRetryMiddleware(max_retries=1, retry_on=is_transient, on_failure="error",
                             initial_delay=MODEL_RETRY_DELAY_SECONDS),
        # Trong cùng: mỗi lần thử lại cũng được đếm.
        BudgetMiddleware(db, str(user.id)),
    ]
