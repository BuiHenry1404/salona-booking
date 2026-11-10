"""TDD cho `subagents/dispatch.py` (Task 9, spec §4.1 / §5 / §5.1) — hàm
điều phối dùng chung: dựng agent con, gửi MỘT HumanMessage, chạy với trần
thời gian, trích dữ kiện bằng code, ghi phiên chờ qua `WaitingSessionService`
(không qua state graph) và trả `Command(update=...)`.

Model là `scripted` (không Azure); Mongo thật.
"""
from __future__ import annotations

import json
from datetime import timedelta

import structlog.testing
from langchain_core.messages import ToolMessage

from app.agents.salon.subagents.appointment.agent import APPOINTMENT
from app.agents.salon.subagents.dispatch import run_subagent
from app.core.clock import now_utc
from app.core.config import settings
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.appointment import AppointmentService
from app.services.shop import ShopService
from app.services.waiting_session import WaitingSessionService
from tests.fakes import Hang, scripted, tool_call, tool_calls
from tests.salon.subagents import (
    TURN_ID, a_holder, a_runtime, a_user, default_hours, human_text, moment, result,
    seed_services, tomorrow_at, tool_json, use_model,
)

_ALLOWED_UPDATE_KEYS = {"messages", "turn_facts", "proposals_created", "failed_calls"}


async def _run(db, user, *, continue_session=False, service_ids=(), request="Book a slot",
               question="", proposals_created=0):
    return await run_subagent(
        APPOINTMENT, request=request, continue_session=continue_session,
        service_ids=list(service_ids),
        runtime=a_runtime(user, question=question, proposals_created=proposals_created),
        db=db, user=user, holder=a_holder(db, user),
    )


async def _open_session(db, user, *, task="Book tomorrow", memory=None, turn_id="turn-0"):
    now = now_utc()
    offered = [moment(tomorrow_at(16)), moment(tomorrow_at(17))]
    await WaitingSessionService(db).open(WaitingSession(
        user_id=str(user.id), agent="appointment", task=task,
        memory=memory if memory is not None else {
            "day": tomorrow_at(16).date().isoformat(), "service_ids": [], "offered": offered,
            "taken": [moment(tomorrow_at(15))], "next_requests": [],
        },
        question=SessionQuestion(
            field="time", options=[SessionOption(**o) for o in offered], text="16:00 or 17:00?",
        ),
        asked_at=now, expires_at=now + timedelta(minutes=10), opened_turn_id=turn_id,
    ))
    return offered


async def _active(db, user):
    return await WaitingSessionService(db).active(str(user.id), now=now_utc())


def _facts(command) -> list[dict]:
    return command.update["turn_facts"]


def _summary(command) -> dict:
    [message] = command.update["messages"]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-parent"
    return json.loads(message.content)


async def _taken_at_15(db):
    await default_hours(db)
    other = await a_user(db, phone="0987654321", name="Chú Ba")
    await AppointmentService(db).create(other, tomorrow_at(15), None)
    return await a_user(db)


async def test_ok_after_a_refused_proposal_opens_a_time_session(test_db, monkeypatch):
    # Live run 2, turn 2, replayed: 15:00 is taken, the model finishes with ok.
    user = await _taken_at_15(test_db)
    use_model(monkeypatch, scripted([
        tool_call("parse_time", {"day_offset": 1, "hour": 3, "part_of_day": "chieu"}),
        tool_call("propose_appointment", {"start_at": moment(tomorrow_at(15))["iso"]}),
        result(status="ok"),
    ]))

    with structlog.testing.capture_logs() as logs:
        command = await _run(test_db, user)

    summary = _summary(command)
    assert (summary["status"], summary["question_field"]) == ("need_info", "time")
    assert summary["options"]
    session = (await _active(test_db, user))["appointment"]
    assert session.question.field == "time"
    assert [o.spoken for o in session.question.options] == summary["options"]
    assert any(log["event"] == "subagent_status_corrected" for log in logs)


async def test_availability_question_on_a_taken_time_opens_a_time_session(test_db, monkeypatch):
    # Owner 11/11, option 1: a customer who only asks whether a taken time is free
    # also gets the free times and a session, so "4 giờ đi" books next turn.
    user = await _taken_at_15(test_db)
    use_model(monkeypatch, scripted([
        tool_call("find_free_slots", {"day": tomorrow_at(15).date().isoformat(), "around": "15:00"}),
        result(status="ok"),
    ]))

    command = await _run(test_db, user, request="Is 3pm tomorrow free?")

    assert _summary(command)["status"] == "need_info"
    assert (await _active(test_db, user))["appointment"].question.field == "time"


async def test_need_info_opens_a_session_with_code_extracted_options(test_db, monkeypatch):
    # Tiệm 15:00-18:00; khách khác chiếm 15:00-16:00, 16:15-17:00, 17:15-18:00
    # → với dịch vụ 15' của khách: 15:00 kín, chỉ 16:00 và 17:00 còn trống.
    await ShopService(test_db).set_hours("15:00", "18:00", [])
    await seed_services(test_db)
    other = await a_user(test_db, phone="0987654321", name="Chú Ba")
    appointments = AppointmentService(test_db)
    await appointments.create(other, tomorrow_at(15), None, service_ids=["blk-60"])
    await appointments.create(other, tomorrow_at(16, 15), None, service_ids=["blk-45"])
    await appointments.create(other, tomorrow_at(17, 15), None, service_ids=["blk-45"])
    user = await a_user(test_db)

    day = tomorrow_at(15).date().isoformat()
    model = scripted([
        tool_call("find_free_slots", {"day": day, "around": "15:00"}),
        # Chữ model nêu giờ khác (14:00) — options vẫn chỉ lấy từ ToolMessage.
        result(status="need_info", question_field="time",
               question_text="15:00 is taken. Ask: 14:00, 16:00 or 17:00?"),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, service_ids=["nail-sua"])

    session = (await _active(test_db, user))["appointment"]
    assert [o.iso for o in session.question.options] == [
        moment(tomorrow_at(16))["iso"], moment(tomorrow_at(17))["iso"],
    ]
    assert session.question.field == "time"
    assert session.opened_turn_id == TURN_ID
    assert session.memory["offered"] == [moment(tomorrow_at(16)), moment(tomorrow_at(17))]
    assert session.memory["taken"] == [moment(tomorrow_at(15))]
    assert session.memory["day"] == day
    assert session.memory["service_ids"] == ["nail-sua"]

    assert set(command.update) <= _ALLOWED_UPDATE_KEYS
    facts = _facts(command)
    assert {"kind": "time", "role": "taken", "moment": moment(tomorrow_at(15))} in facts
    assert {"kind": "time", "role": "free", "moment": moment(tomorrow_at(16))} in facts
    assert {"kind": "time", "role": "free", "moment": moment(tomorrow_at(17))} in facts
    assert facts[-1] == {"kind": "agent_status", "agent": "appointment", "status": "need_info"}
    summary = _summary(command)
    assert summary["status"] == "need_info"
    assert summary["question_field"] == "time"
    assert summary["options"] == [moment(tomorrow_at(16))["spoken"], moment(tomorrow_at(17))["spoken"]]
    assert "failed_calls" not in command.update


async def test_continue_session_passes_memory_in_the_message(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    offered = await _open_session(test_db, user)
    model = scripted([result(status="ok")])
    use_model(monkeypatch, model)

    await _run(test_db, user, continue_session=True, request="Customer picked a time")

    text = human_text(model)
    assert "Session memory:" in text
    for option in offered:
        assert option["spoken"] in text
        assert option["iso"] in text
    assert text.index("Session memory:") < text.index("Task: Customer picked a time")


async def test_continue_session_keeps_the_service_of_the_session(test_db, monkeypatch):
    # Live run 4, turn 8: the session was opened for a haircut, the Orchestrator
    # continued it passing sơn gel, and 17:00 was booked as sơn gel. On a continued
    # session, the service the session was opened with wins.
    await default_hours(test_db)
    await seed_services(test_db)
    user = await a_user(test_db)
    await _open_session(test_db, user, memory={
        "day": tomorrow_at(16).date().isoformat(), "service_ids": ["toc-nhuom"],
        "offered": [moment(tomorrow_at(16))], "taken": [], "next_requests": [],
    })
    use_model(monkeypatch, scripted([
        tool_call("parse_time", {"day_offset": 1, "hour": 4, "part_of_day": "chieu"}),
        tool_call("propose_appointment", {"start_at": moment(tomorrow_at(16))["iso"]}),
        result(status="ok"),
    ]))

    with structlog.testing.capture_logs() as logs:
        command = await _run(test_db, user, continue_session=True, service_ids=["blk-45"],
                             request="The customer chooses 4 pm")

    [proposal] = [f for f in _facts(command) if f["kind"] == "proposal"]
    assert proposal["service_ids"] == ["toc-nhuom"]
    assert any(log["event"] == "subagent_service_ids_from_session" for log in logs)


async def test_ok_closes_the_session(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _open_session(test_db, user)
    use_model(monkeypatch, scripted([result(status="ok")]))

    command = await _run(test_db, user, continue_session=True)

    assert "appointment" not in await _active(test_db, user)
    assert _facts(command)[-1] == {"kind": "agent_status", "agent": "appointment", "status": "ok"}


async def test_proposal_with_next_requests_keeps_the_session_open(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    day = tomorrow_at(9).date().isoformat()
    model = scripted([
        tool_calls(
            ("parse_time", {"day": day, "hour": 9, "part_of_day": "sang"}),
            ("parse_time", {"day": day, "hour": 3, "part_of_day": "chieu"}),
        ),
        tool_call("remember_next_request", {"iso": tomorrow_at(15).isoformat()}),
        tool_call("propose_appointment", {"start_at": tomorrow_at(9).isoformat(), "note": None}),
        result(status="ok"),
    ])
    use_model(monkeypatch, model)

    before = now_utc()
    command = await _run(test_db, user, request="Book 9am and 3pm tomorrow")

    session = (await _active(test_db, user))["appointment"]
    assert session.question.field == "confirm"
    assert session.memory["next_requests"] == [moment(tomorrow_at(15))]
    ttl = timedelta(minutes=settings.salon_session_ttl_minutes)
    assert before + ttl <= session.expires_at <= now_utc() + ttl
    assert session.opened_turn_id == TURN_ID
    assert command.update["proposals_created"] == 1
    proposal_facts = [f for f in _facts(command) if f["kind"] == "proposal"]
    assert [f["at"] for f in proposal_facts] == [moment(tomorrow_at(9))]


async def test_refused_second_propose_keeps_the_next_request(test_db, monkeypatch):
    # "3 giờ cho chị, 4 giờ cho con gái": model đề xuất cả hai trong một vòng —
    # lần hai bị từ chối theo hạn mức, yêu cầu 4 giờ vẫn phải còn trong phiên.
    await default_hours(test_db)
    user = await a_user(test_db)
    day = tomorrow_at(15).date().isoformat()
    model = scripted([
        tool_calls(
            ("parse_time", {"day": day, "hour": 3, "part_of_day": "chieu"}),
            ("parse_time", {"day": day, "hour": 4, "part_of_day": "chieu"}),
        ),
        tool_call("remember_next_request", {"iso": tomorrow_at(16).isoformat()}),
        tool_calls(
            ("propose_appointment", {"start_at": tomorrow_at(15).isoformat()}),
            ("propose_appointment", {"start_at": tomorrow_at(16).isoformat()}),
        ),
        result(status="ok"),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, request="Book 3pm for her and 4pm for her daughter")

    refused = [m for m in tool_json(model, "propose_appointment") if "error" in m]
    assert [m["error"] for m in refused] == ["one_proposal_per_call"]
    session = (await _active(test_db, user))["appointment"]
    assert session.question.field == "confirm"
    assert session.memory["next_requests"] == [moment(tomorrow_at(16))]
    assert command.update["proposals_created"] == 1


async def test_a_taken_next_request_is_dropped_from_the_session(test_db, monkeypatch):
    # Final review 11/11: "9 giờ và 4 giờ" with 4pm taken. The refused next request
    # must leave next_requests, or confirm → _continue_next asks 4pm again forever.
    await default_hours(test_db)
    other = await a_user(test_db, phone="0987654321", name="Chú Ba")
    await AppointmentService(test_db).create(other, tomorrow_at(16), None)
    user = await a_user(test_db)
    await _open_session(test_db, user, memory={
        "day": tomorrow_at(16).date().isoformat(), "service_ids": [], "offered": [],
        "taken": [], "next_requests": [moment(tomorrow_at(16))],
    })
    use_model(monkeypatch, scripted([
        tool_call("parse_time", {"day_offset": 1, "hour": 4, "part_of_day": "chieu"}),
        tool_call("propose_appointment", {"start_at": moment(tomorrow_at(16))["iso"]}),
        result(status="ok"),
    ]))

    await _run(test_db, user, continue_session=True, request="Book the next requested time")

    session = (await _active(test_db, user))["appointment"]
    assert session.question.field == "time"
    assert session.memory["next_requests"] == []


async def test_alternatives_are_not_next_requests(test_db, monkeypatch):
    # "3 giờ hoặc 4 giờ": hai lần parse_time nhưng không remember → không có yêu cầu kế.
    await default_hours(test_db)
    user = await a_user(test_db)
    day = tomorrow_at(15).date().isoformat()
    use_model(monkeypatch, scripted([
        tool_calls(
            ("parse_time", {"day": day, "hour": 3, "part_of_day": "chieu"}),
            ("parse_time", {"day": day, "hour": 4, "part_of_day": "chieu"}),
        ),
        tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat()}),
        result(status="ok"),
    ]))

    command = await _run(test_db, user, request="Book 3pm or 4pm")

    assert "appointment" not in await _active(test_db, user)
    assert command.update["proposals_created"] == 1


async def test_timeout_is_failed_and_keeps_the_older_session(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _open_session(test_db, user, task="Older task")
    monkeypatch.setattr(settings, "orch_task_timeout_seconds", 0.2)
    use_model(monkeypatch, scripted([Hang(5)]))

    command = await _run(test_db, user, continue_session=True)

    assert command.update["failed_calls"] == ["appointment"]
    assert _facts(command)[-1] == {"kind": "agent_status", "agent": "appointment", "status": "failed"}
    summary = _summary(command)
    assert summary["status"] == "failed"
    assert summary["error_code"] == "timeout"
    session = (await _active(test_db, user))["appointment"]
    assert session.task == "Older task"
    assert session.opened_turn_id == "turn-0"


async def test_new_task_replaces_the_waiting_session(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _open_session(test_db, user, task="Older task")
    model = scripted([
        result(status="need_info", question_field="day", question_text="Which day?"),
    ])
    use_model(monkeypatch, model)

    await _run(test_db, user, continue_session=False, request="Customer wants another service")

    assert "Session memory:" not in human_text(model)
    session = (await _active(test_db, user))["appointment"]
    assert session.task == "Customer wants another service"
    assert session.question.field == "day"
    assert session.question.options == []
    assert session.memory.get("offered") == []
    assert session.memory.get("taken") == []
    assert session.opened_turn_id == TURN_ID
