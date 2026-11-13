"""TDD cho Task 4: `parse_time` dùng buổi khách đã gõ khi model bỏ sót
`part_of_day` (live run 5 — khách gõ "8 giờ sáng đi em", model chỉ điền
hour=8, `parse_vi_time` hỏi lại "sáng hay chiều" dù khách đã nói rồi).
"""
from __future__ import annotations

import json

from app.agents.salon.subagents.time_tool import parse_time, period_from_customer
from tests.salon.subagents import a_runtime, a_user, default_hours, moment, tomorrow_at


def test_period_from_customer_reads_the_typed_period():
    assert period_from_customer("8 giờ sáng đi em", 8) == "sang"
    assert period_from_customer("1 giờ chiều đi em", 1) == "chieu"


def test_parse_time_keeps_asking_when_periods_conflict():
    assert period_from_customer("8 giờ sáng hay 8 giờ tối", 8) is None


def test_parse_time_without_customer_text_is_unchanged():
    assert period_from_customer("", 8) is None


async def test_parse_time_uses_the_typed_period_when_the_model_omits_it(test_db):
    await default_hours(test_db)
    user = await a_user(test_db)
    runtime = a_runtime(user, question="8 giờ sáng đi em")

    result = json.loads(await parse_time.ainvoke(
        {"day_offset": 1, "hour": 8, "runtime": runtime}))

    assert result == moment(tomorrow_at(8))


def test_parse_time_schema_hides_runtime():
    assert "runtime" not in parse_time.args
