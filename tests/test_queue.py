"""Hàng chờ đặt lần lượt (lỗi live B2 #2): middleware, dòng bối cảnh, supervisor."""
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage

from app.agents.booking_graph import supervisor
from app.agents.booking_graph.context import (QUEUE_MAX_AGE_MINUTES, live_queue,
                                              queue_offer, queued_line)
from app.agents.booking_graph.middleware.confirm import next_queue
from app.core.clock import TZ, now_utc

LATER = (datetime.now(TZ) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
NINE = LATER.replace(hour=9)


def item(start, note="cắt tóc cho thằng cháu", queued_at=None):
    return {"start_at": start.isoformat(), "note": note,
            "queued_at": (queued_at or now_utc()).isoformat()}


def held(start, queued=None):
    data = {"held": {"iso": start.isoformat()}}
    if queued is not None:
        data["queued"] = [{"at": {"iso": q.isoformat()}, "note": "n"} for q in queued]
    return data


def test_tool_queue_replaces_the_old_one():
    queue = next_queue(held(NINE, [LATER]), [item(NINE.replace(hour=11))])
    assert [q["start_at"] for q in queue] == [LATER.isoformat()]


def test_no_queue_from_the_tool_keeps_the_old_one_minus_the_held_time():
    queue = next_queue(held(LATER), [item(LATER), item(NINE.replace(hour=16))])
    assert [q["start_at"] for q in queue] == [NINE.replace(hour=16).isoformat()]


def test_empty_queue_from_the_tool_clears_it():
    assert next_queue(held(NINE, []), [item(LATER)]) == []


def test_live_queue_drops_past_and_stale_items():
    stale = now_utc() - timedelta(minutes=QUEUE_MAX_AGE_MINUTES + 1)
    queue = [item(LATER), item(datetime(2020, 1, 1, 9, tzinfo=TZ)), item(LATER, queued_at=stale),
             {"bad": 1}]
    assert live_queue(queue) == [queue[0]]


def test_queued_line_and_offer_are_code_written():
    line = queued_line([item(LATER)])
    assert "[queued, iso: " + LATER.isoformat() + "]" in line
    assert "cắt tóc cho thằng cháu" in line
    assert queued_line([]) == ""
    offer = queue_offer([item(LATER)], "anh Hùng")
    assert "anh Hùng" in offer and "cắt tóc cho thằng cháu" in offer and offer.endswith("?")


async def test_supervisor_routes_to_booking_while_a_request_waits(monkeypatch):
    def boom(**kw):
        raise AssertionError("supervisor model must not be called")
    monkeypatch.setattr(supervisor, "build_chat_model", boom)
    out = await supervisor.supervise({"messages": [HumanMessage("ừ")], "queued": [item(LATER)]})
    assert out == {"route": "booking"}
