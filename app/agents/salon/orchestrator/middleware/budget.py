"""Ngân sách lời gọi LLM theo khách (spec §3.3 hàng "Ngân sách", §8).

`BudgetMiddleware.awrap_model_call` đếm MỖI lời gọi model thật (kể cả lần thử
lại — nó nằm trong cùng, dưới `ModelRetryMiddleware`) vào khoá
`llm:user:{id}`, cửa sổ 1 giờ, trần `orch_llm_calls_per_user_per_hour`. Đã
chạm trần → `LlmBudgetExceeded`, KHÔNG gọi model; `run_turn` bắt lỗi và trả
câu mẫu code.

Dùng cho Orchestrator VÀ mọi agent con (`orchestrator/tools.py` truyền vào
`run_subagent`). Lời gọi của `SummarizationMiddleware` gọi thẳng model, không
qua `wrap_model_call`, nên KHÔNG được đếm — chấp nhận (một lời gọi nén nhỏ khi
thread vượt ngưỡng).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)

LLM_KEY = "llm:user:{user_id}"
WINDOW_SECONDS = 3600


class LlmBudgetExceeded(Exception):
    """Khách đã dùng hết số lời gọi LLM trong giờ gần nhất."""


class BudgetMiddleware(AgentMiddleware[Any, Any]):
    def __init__(self, db: AsyncIOMotorDatabase, user_id: str) -> None:
        super().__init__()
        self.rate_limit = RateLimitService(db)
        self.user_id = user_id
        self.key = LLM_KEY.format(user_id=user_id)

    async def awrap_model_call(
        self, request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            await self.rate_limit.check(self.key, settings.orch_llm_calls_per_user_per_hour,
                                        WINDOW_SECONDS)
        except RateLimitedError as exc:
            # Agent con không có context của lượt — log những gì có.
            context = request.runtime.context if request.runtime is not None else None
            logger.warning("llm_budget_exceeded", extra={
                "user_id": self.user_id, "turn_id": getattr(context, "turn_id", None),
                "thread_id": getattr(context, "thread_id", None),
            })
            raise LlmBudgetExceeded(self.user_id) from exc
        await self.rate_limit.hit(self.key, WINDOW_SECONDS)
        return await handler(request)
