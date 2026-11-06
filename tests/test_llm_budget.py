"""Task 5.2: `app/agents/booking/llm_budget.py` — trần chi phí LLM theo khách
(§7.3 spec). `LlmBudget` chỉ đóng gói `RateLimitService` với key
`llm:user:<id>`; `BudgetMiddleware` ghi dấu TRƯỚC mỗi lời gọi model thật
(`awrap_model_call`), không bao giờ chặn `handler`.
"""
from __future__ import annotations

import pytest
from langchain.agents.middleware import ModelRequest

from app.agents.booking.llm_budget import BudgetMiddleware, LlmBudget, count_model_call
from app.core.config import settings

pytestmark = pytest.mark.asyncio


async def test_hit_counts_per_user(test_db):
    budget = LlmBudget(test_db, "user-1")
    await budget.hit()
    await budget.hit()
    assert await test_db["rate_limits"].count_documents({"key": "llm:user:user-1"}) == 2


async def test_over_after_the_limit(test_db):
    budget = LlmBudget(test_db, "user-2")
    limit = settings.orch_llm_calls_per_user_per_hour
    for _ in range(limit - 1):
        await budget.hit()
        assert await budget.over() is False
    await budget.hit()
    assert await budget.over() is True


async def test_two_users_have_separate_budgets(test_db):
    a = LlmBudget(test_db, "user-a")
    b = LlmBudget(test_db, "user-b")
    for _ in range(settings.orch_llm_calls_per_user_per_hour):
        await a.hit()
    assert await a.over() is True
    assert await b.over() is False


class _FakeHandler:
    def __init__(self):
        self.calls = 0
        self.hit_before_call: bool | None = None

    async def __call__(self, request, budget, test_db):
        self.calls += 1
        count = await test_db["rate_limits"].count_documents({"key": "llm:user:user-mw"})
        self.hit_before_call = count >= 1
        return "ok"


async def test_budget_middleware_hits_before_each_model_call(test_db):
    budget = LlmBudget(test_db, "user-mw")
    middleware = BudgetMiddleware(budget)
    fake = _FakeHandler()

    async def handler(request: ModelRequest):
        return await fake(request, budget, test_db)

    result = await middleware.awrap_model_call(object(), handler)

    assert result == "ok"
    assert fake.calls == 1
    assert fake.hit_before_call is True
    assert await test_db["rate_limits"].count_documents({"key": "llm:user:user-mw"}) == 1


async def test_count_model_call_hits_budget(test_db):
    budget = LlmBudget(test_db, "user-conv")
    await count_model_call(budget)
    assert await test_db["rate_limits"].count_documents({"key": "llm:user:user-conv"}) == 1
