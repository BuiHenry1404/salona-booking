"""Test tự kiểm cho helper dựng tool call trong `tests/fakes.py` (Task 2.1).

`tool_call`/`tool_calls` dùng để kịch bản hoá `ScriptedChatModel` trả tool
call trong test agent con/orchestrator (M2+), không gọi model thật.
"""
from langchain_core.messages import AIMessage

from app.agents.booking.schemas import AppointmentArgs, ShopArgs, TaskSpec
from tests.factories import make_spec
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


def test_make_spec_builds_a_valid_task_spec_with_defaults():
    spec = make_spec()

    assert isinstance(spec, TaskSpec)
    assert spec.agent == "appointment"
    assert spec.action == "resolve_time"
    assert spec.task_id == "t1.0"
    assert isinstance(spec.args, AppointmentArgs)
    # Mọi field bắt buộc nhưng không truyền → mặc định None (strict-safe schema).
    assert spec.args.time is None
    assert spec.args.service is None
    assert spec.args.appointment_id is None
    assert spec.args.for_whom is None
    assert spec.args.also is None


def test_make_spec_accepts_overrides_and_shop_agent():
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t2.1")

    assert spec.agent == "shop_info"
    assert spec.action == "get_hours"
    assert spec.task_id == "t2.1"
    assert isinstance(spec.args, ShopArgs)
    assert spec.args.time is None


def test_make_spec_passes_through_extra_args():
    spec = make_spec(service="cắt tóc")

    assert spec.args.service == "cắt tóc"
