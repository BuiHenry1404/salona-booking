"""Test cho `app/agents/booking/specialists/guard.py` (Task 2.2).

`allowlist` giới hạn tool theo action (§8.1/§8.2 thiết kế); `OneProposalPerTaskMiddleware`
chặn lần tạo đề xuất thứ hai trong CÙNG một task con (C3, §9.5) — đếm đề xuất đã
TẠO THÀNH CÔNG (ToolMessage có `proposal_id`), không đếm số lần gọi: một lần thất
bại (`error_code` khác) không chặn lần thử lại.
"""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.agents.booking.specialists.guard import (PROPOSAL_TOOLS, allowlist,
                                                   OneProposalPerTaskMiddleware)
from tests.fakes import tool_call

pytestmark = pytest.mark.asyncio


@tool
def parse_time(text: str) -> str:
    """Phân giải thời gian."""
    return "{}"


@tool
def propose_appointment(start_at: str) -> str:
    """Tạo đề xuất đặt lịch."""
    return "{}"


@tool
def cancel_appointment(appointment_id: str) -> str:
    """Tạo đề xuất huỷ lịch."""
    return "{}"


@tool
def find_free_slots(day: str) -> str:
    """Tìm slot trống."""
    return "{}"


ALL_TOOLS = [parse_time, propose_appointment, cancel_appointment, find_free_slots]


def test_allowlist_keeps_only_named_tools_in_order():
    result = allowlist(ALL_TOOLS, ["propose_appointment", "parse_time"])

    assert [t.name for t in result] == ["propose_appointment", "parse_time"]


def test_allowlist_raises_on_unknown_name():
    with pytest.raises(ValueError):
        allowlist(ALL_TOOLS, ["parse_time", "khong_ton_tai"])


async def recording_handler(request):
    recording_handler.calls.append(request)
    return ToolMessage(content='{"ok": true}', tool_call_id=request.tool_call["id"],
                        name=request.tool_call["name"])


def _request(tool_call_dict, messages):
    recording_handler.calls = []
    return SimpleNamespace(tool_call=tool_call_dict, tool=None, state={"messages": messages}, runtime=None)


async def test_second_proposal_tool_call_in_a_task_is_rejected():
    human = HumanMessage(content="đặt lịch mai 3h")
    first_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c1")
    first_result = ToolMessage(content='{"proposal_id": "p1"}', tool_call_id="c1",
                                name="propose_appointment")
    second_ai = tool_call("propose_appointment", {"start_at": "y"}, id="c2")
    messages = [human, first_ai, first_result, second_ai]

    request = _request(second_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert recording_handler.calls == []
    assert result.status == "error"
    assert "one_proposal_per_task" in result.content
    assert result.tool_call_id == "c2"
    assert result.name == "propose_appointment"


async def test_retry_after_a_failed_proposal_is_allowed():
    """Lần thử trước lỗi (không có `proposal_id`) → không tính là đã tạo đề xuất."""
    human = HumanMessage(content="đặt lịch mai 3h")
    first_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c1")
    first_result = ToolMessage(content='{"error_code": "slot_unavailable"}', tool_call_id="c1",
                                name="propose_appointment", status="error")
    second_ai = tool_call("propose_appointment", {"start_at": "y"}, id="c2")
    messages = [human, first_ai, first_result, second_ai]

    request = _request(second_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert len(recording_handler.calls) == 1
    assert result.content == '{"ok": true}'


async def test_first_proposal_tool_call_passes_through():
    human = HumanMessage(content="đặt lịch mai 3h")
    first_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c1")
    messages = [human, first_ai]

    request = _request(first_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert len(recording_handler.calls) == 1
    assert result.content == '{"ok": true}'


async def test_read_tools_are_never_blocked():
    """`parse_time` không phải đề xuất — gọi bao nhiêu lần cũng được."""
    human = HumanMessage(content="đặt lịch mai 3h")
    first_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c1")
    first_result = ToolMessage(content='{"proposal_id": "p1"}', tool_call_id="c1",
                                name="propose_appointment")
    second_ai = tool_call("parse_time", {"text": "mai"}, id="c2")
    messages = [human, first_ai, first_result, second_ai]

    request = _request(second_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert len(recording_handler.calls) == 1
    assert result.content == '{"ok": true}'


async def test_non_json_tool_message_content_counts_as_not_created():
    human = HumanMessage(content="đặt lịch mai 3h")
    first_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c1")
    first_result = ToolMessage(content="không parse được", tool_call_id="c1",
                                name="propose_appointment")
    second_ai = tool_call("propose_appointment", {"start_at": "y"}, id="c2")
    messages = [human, first_ai, first_result, second_ai]

    request = _request(second_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert len(recording_handler.calls) == 1
    assert result.content == '{"ok": true}'


async def test_only_counts_messages_after_the_last_human_message():
    """Đề xuất đã tạo ở TASK TRƯỚC (trước HumanMessage cũ) không tính cho task mới."""
    old_human = HumanMessage(content="đặt lịch hôm qua")
    old_ai = tool_call("propose_appointment", {"start_at": "x"}, id="c0")
    old_result = ToolMessage(content='{"proposal_id": "p0"}', tool_call_id="c0",
                              name="propose_appointment")
    new_human = HumanMessage(content="đặt lịch mai 3h")
    new_ai = tool_call("propose_appointment", {"start_at": "y"}, id="c1")
    messages = [old_human, old_ai, old_result, new_human, new_ai]

    request = _request(new_ai.tool_calls[0], messages)
    middleware = OneProposalPerTaskMiddleware()
    result = await middleware.awrap_tool_call(request, recording_handler)

    assert len(recording_handler.calls) == 1
    assert result.content == '{"ok": true}'


def test_proposal_tools_constant():
    assert PROPOSAL_TOOLS == ("propose_appointment", "cancel_appointment")
