"""Test tự kiểm cho helper dựng tool call trong `tests/fakes.py` (Task 2.1).

`tool_call`/`tool_calls` dùng để kịch bản hoá `ScriptedChatModel` trả tool
call trong test agent con/orchestrator (M2+), không gọi model thật.
"""
from langchain_core.messages import AIMessage

from tests.fakes import tool_call, tool_calls


def test_tool_call_builds_one_ai_message_with_one_call():
    msg = tool_call("parse_time", {"text": "mai"})

    assert isinstance(msg, AIMessage)
    assert msg.content == ""
    assert len(msg.tool_calls) == 1
    call = msg.tool_calls[0]
    assert call["name"] == "parse_time"
    assert call["args"] == {"text": "mai"}
    assert isinstance(call["id"], str) and call["id"]


def test_tool_call_ids_are_unique_when_not_given():
    first = tool_call("parse_time", {})
    second = tool_call("parse_time", {})

    assert first.tool_calls[0]["id"] != second.tool_calls[0]["id"]


def test_tool_call_accepts_explicit_id():
    msg = tool_call("parse_time", {}, id="fixed-id")

    assert msg.tool_calls[0]["id"] == "fixed-id"


def test_tool_calls_builds_one_message_with_n_calls():
    msg = tool_calls(
        ("parse_time", {"text": "mai"}),
        ("find_free_slots", {"day": "2026-11-01"}),
    )

    assert isinstance(msg, AIMessage)
    assert len(msg.tool_calls) == 2
    assert [c["name"] for c in msg.tool_calls] == ["parse_time", "find_free_slots"]
    assert msg.tool_calls[0]["args"] == {"text": "mai"}
    assert msg.tool_calls[1]["args"] == {"day": "2026-11-01"}
    # ID khác nhau trong cùng một AIMessage.
    assert msg.tool_calls[0]["id"] != msg.tool_calls[1]["id"]
