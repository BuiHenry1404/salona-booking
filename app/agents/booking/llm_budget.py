"""Task 5.2: trần chi phí LLM theo khách (§7.3 spec).

`RateLimitService` đã có `hit`/`check` (đóng danh sách §1.3 — không sửa
service ở đây). `LlmBudget` chỉ đóng gói key `llm:user:<id>` và gọi hai hàm
đó; `BudgetMiddleware` gắn `hit()` vào MỌI lời gọi model của Orchestrator và
hai specialist (Task 5.3 wiring); `count_model_call` là lối gọi thẳng cho
Conversation (model `respond` không chạy qua `create_agent`/middleware).

Quá trần KHÔNG chặn lượt — `hit()` luôn ghi dấu, không bao giờ ném lỗi.
`over()` chỉ đọc, dùng ở graph node `orchestrate` (Task 5.3) để ép
`OrchestratorDeps.max_rounds = 2`.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.errors import RateLimitedError
from app.services.rate_limit import RateLimitService

LLM_KEY = "llm:user:{user_id}"

WINDOW_SECONDS = 3600


class LlmBudget:
    """Đếm số lời gọi LLM của một khách trong 1 giờ gần nhất."""

    def __init__(self, db: AsyncIOMotorDatabase, user_id: str):
        self._svc = RateLimitService(db)
        self._key = LLM_KEY.format(user_id=user_id)

    async def hit(self) -> None:
        """Ghi một dấu — gọi mỗi khi THỰC SỰ gửi một lời gọi model, không
        phải mỗi tool call."""
        await self._svc.hit(self._key, WINDOW_SECONDS)

    async def over(self) -> bool:
        """Đã chạm `settings.orch_llm_calls_per_user_per_hour` trong giờ gần
        nhất chưa — `RateLimitService.check` ném lỗi khi đã đạt trần, bắt lại
        thành `bool` vì đây không phải chỗ chặn request."""
        try:
            await self._svc.check(self._key, settings.orch_llm_calls_per_user_per_hour, WINDOW_SECONDS)
        except RateLimitedError:
            return True
        return False


class BudgetMiddleware(AgentMiddleware):
    """Ghi một dấu budget TRƯỚC mỗi lời gọi model thật — gắn vào Orchestrator
    và hai specialist (Task 5.3). Không bao giờ chặn `handler`: vượt trần chỉ
    làm `OrchestratorDeps.max_rounds` bị ép còn 2 (quyết định ở graph node
    `orchestrate`, không phải ở đây)."""

    def __init__(self, budget: LlmBudget):
        self._budget = budget

    async def awrap_model_call(self, request: ModelRequest, handler):
        await self._budget.hit()
        return await handler(request)


async def count_model_call(budget: LlmBudget) -> None:
    """Conversation (model `respond`) không chạy qua `create_agent` nên
    không có middleware — gọi thẳng hàm này trước `ainvoke`."""
    await budget.hit()
