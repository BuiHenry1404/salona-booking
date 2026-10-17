"""Test cho `bench/judge.py` — client Claude giả, không gọi mạng thật."""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from bench.config import load_settings
from bench.judge import JUDGE_AXES, judge, render_transcript
from bench.llm import make_client


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc

    async def create(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.messages = _FakeMessages(response=response, exc=exc)


def _valid_json() -> str:
    data = {axis: {"score": 4, "why": f"ly do {axis}"} for axis in JUDGE_AXES}
    data["worst_line"] = "Da anh/chi vui long cho em biet them a."
    return json.dumps(data)


async def test_judge_parses_scores():
    client = _FakeClient(response=_FakeResponse(_valid_json()))

    result = await judge(client, "claude-sonnet-4-6", "Khách: xin chào\nAI: dạ em chào anh/chị\n")

    assert result is not None
    for axis in JUDGE_AXES:
        assert result[axis]["score"] == 4
        assert "why" in result[axis]
    assert "worst_line" in result


async def test_judge_failure_yields_none_on_refusal():
    client = _FakeClient(response=_FakeResponse("", stop_reason="refusal"))

    result = await judge(client, "claude-sonnet-4-6", "transcript bất kỳ")

    assert result is None


async def test_judge_failure_yields_none_on_bad_json():
    client = _FakeClient(response=_FakeResponse("đây không phải JSON {{{"))

    result = await judge(client, "claude-sonnet-4-6", "transcript bất kỳ")

    assert result is None


@pytest.mark.parametrize("bad_score", [0, 99, "cao", True])
async def test_judge_failure_yields_none_on_invalid_score(bad_score):
    data = {axis: {"score": 4, "why": f"ly do {axis}"} for axis in JUDGE_AXES}
    data["lap_y"]["score"] = bad_score
    data["worst_line"] = "Da anh/chi vui long cho em biet them a."
    client = _FakeClient(response=_FakeResponse(json.dumps(data)))

    result = await judge(client, "claude-sonnet-4-6", "transcript bất kỳ")

    assert result is None


async def test_judge_failure_yields_none_on_api_error():
    request = httpx2.Request("POST", "https://example.invalid/v1/messages")
    client = _FakeClient(exc=anthropic.APIConnectionError(request=request))

    result = await judge(client, "claude-sonnet-4-6", "transcript bất kỳ")

    assert result is None


def test_render_transcript_formats_turns_with_tools():
    from bench.adapters.base import TurnRecord

    turns = [
        TurnRecord(index=0, customer_text="cho em hỏi giờ mở cửa", answer="dạ 8h-19h ạ", tools=[]),
        TurnRecord(
            index=1,
            customer_text="đặt 10h sáng mai",
            answer="dạ em đặt xong rồi ạ",
            tools=["propose_appointment"],
        ),
    ]

    rendered = render_transcript(turns)

    assert "Khách: cho em hỏi giờ mở cửa" in rendered
    assert "AI: dạ 8h-19h ạ" in rendered
    assert "AI: dạ em đặt xong rồi ạ (propose_appointment)" in rendered


def test_render_transcript_empty_list():
    assert render_transcript([]) == ""


def test_make_client_returns_async_foundry_client():
    settings = load_settings(env_path=None)
    settings.foundry_resource = "my-resource"
    settings.foundry_api_key = "my-key"

    client = make_client(settings)

    assert isinstance(client, anthropic.AsyncAnthropicFoundry)


@pytest.mark.live
async def test_judge_real():
    settings = load_settings()
    if not settings.foundry_resource or not settings.foundry_api_key:
        pytest.skip("cần bench/.env với BENCH_FOUNDRY_RESOURCE/BENCH_FOUNDRY_API_KEY")

    client = make_client(settings)
    transcript = "Khách: cho em hỏi giờ mở cửa\nAI: dạ tiệm mở 8h đến 19h ạ\n"

    result = await judge(client, settings.judge_model, transcript)

    assert result is not None
    for axis in JUDGE_AXES:
        assert 1 <= result[axis]["score"] <= 5
