"""Test cho `bench/simulate.py` — client Claude giả, không gọi mạng thật."""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from bench.adapters.base import TurnRecord
from bench.scenario import SimGoal
from bench.simulate import DONE_TOKEN, SimCustomer


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses=None, exc: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses=None, exc: Exception | None = None) -> None:
        self.messages = _FakeMessages(responses=responses, exc=exc)


def _turn(index: int, text: str = "dạ vâng ạ", answer: str = "dạ em đã ghi nhận") -> TurnRecord:
    return TurnRecord(index=index, customer_text=text, answer=answer)


async def test_sim_stops_on_done_token():
    client = _FakeClient(responses=[_FakeResponse(DONE_TOKEN)])
    goal = SimGoal(goal="đặt lịch cắt tóc", persona="bà cụ lớn tuổi", max_turns=8)
    sim = SimCustomer(client, "claude-sonnet-4-6", goal)

    result = await sim.next_message([_turn(0)])

    assert result is None


async def test_sim_returns_next_customer_message():
    client = _FakeClient(responses=[_FakeResponse("dạ cho cô đặt 9 giờ sáng mai")])
    goal = SimGoal(goal="đặt lịch cắt tóc", persona="bà cụ lớn tuổi", max_turns=8)
    sim = SimCustomer(client, "claude-sonnet-4-6", goal)

    result = await sim.next_message([])

    assert result == "dạ cho cô đặt 9 giờ sáng mai"


async def test_sim_stops_at_max_turns():
    client = _FakeClient(responses=[_FakeResponse("sẽ không bao giờ được gọi")])
    goal = SimGoal(goal="đặt lịch", persona="ông cụ", max_turns=2)
    sim = SimCustomer(client, "claude-sonnet-4-6", goal)

    result = await sim.next_message([_turn(0), _turn(1)])

    assert result is None
    assert client.messages.calls == []


async def test_sim_stops_on_refusal():
    client = _FakeClient(responses=[_FakeResponse("", stop_reason="refusal")])
    goal = SimGoal(goal="đặt lịch", persona="ông cụ", max_turns=8)
    sim = SimCustomer(client, "claude-sonnet-4-6", goal)

    result = await sim.next_message([])

    assert result is None


async def test_sim_stops_on_api_error():
    request = httpx2.Request("POST", "https://example.invalid/v1/messages")
    client = _FakeClient(exc=anthropic.APIConnectionError(request=request))
    goal = SimGoal(goal="đặt lịch", persona="ông cụ", max_turns=8)
    sim = SimCustomer(client, "claude-sonnet-4-6", goal)

    result = await sim.next_message([])

    assert result is None
