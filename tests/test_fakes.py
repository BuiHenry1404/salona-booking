"""Test tự kiểm cho helper dựng tool call trong `tests/fakes.py` (Task 2.1)
và helper dựng câu trả lời cuối Level C trích dữ kiện (`cite`/`reply`/`plain`,
Task 6).

`tool_call`/`tool_calls` dùng để kịch bản hoá `ScriptedChatModel` trả tool
call trong test agent con/orchestrator (M2+), không gọi model thật.
"""
import json

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from tests.fakes import ScriptedChatModel, cite, plain, reply, tool_call, tool_calls


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


BLOCK = ("Today: ...\n\nReply facts (cite by id):\n- t1 [taken]: Thứ Năm 12/11, 3 giờ chiều\n"
         "- t2 [offer]: Thứ Năm 12/11, 1 giờ chiều")


def test_plain_builds_one_plain_segment():
    msg = plain("Dạ, tiệm chào đón anh chị.")

    assert isinstance(msg, AIMessage)
    data = json.loads(msg.content)
    assert data == {"segments": [{"text": "Dạ, tiệm chào đón anh chị.", "ref": None, "role": None}]}


def test_reply_resolves_ids_from_the_context_block():
    msg = reply("Dạ ", cite("taken", "3 giờ chiều mai", find="3 giờ chiều"), " có khách rồi ạ.")(
        [SystemMessage("prompt"), SystemMessage(BLOCK)])

    segments = json.loads(msg.content)["segments"]
    assert segments[0] == {"text": "Dạ ", "ref": None, "role": None}
    assert segments[1] == {"text": "3 giờ chiều mai", "ref": "t1", "role": "taken"}
    assert segments[2] == {"text": " có khách rồi ạ.", "ref": None, "role": None}


def test_cite_with_an_explicit_ref_skips_the_lookup():
    msg = reply(cite("taken", "giờ đó", ref="unknown-id"))(
        [SystemMessage("prompt"), SystemMessage(BLOCK)])

    assert json.loads(msg.content)["segments"][0] == {"text": "giờ đó", "ref": "unknown-id", "role": "taken"}


def test_cite_without_a_matching_fact_raises():
    with pytest.raises(AssertionError, match="no reply fact for offer '5 giờ sáng'"):
        reply(cite("offer", "5 giờ sáng"))([SystemMessage("prompt"), SystemMessage(BLOCK)])


async def test_scripted_model_calls_callable_replies():
    model = ScriptedChatModel(replies=[
        reply("Dạ ", cite("offer", "1 giờ chiều mai", find="1 giờ chiều")),
    ], calls=[], bind_kwargs=[])

    result = await model.ainvoke([SystemMessage("prompt"), SystemMessage(BLOCK)])

    assert json.loads(result.content)["segments"][1] == {
        "text": "1 giờ chiều mai", "ref": "t2", "role": "offer",
    }
