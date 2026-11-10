"""TDD cho tool của Orchestrator và luật kiểm tool call (Task 11, spec §3.1,
§3.3, §4.1; docs/architech-agent.md luật 3, 4, 6).

- Schema bốn tool giao việc/quyết định CỐ ĐỊNH mọi lượt (prompt cache) — id
  dịch vụ kiểm bằng code trong `ToolRulesMiddleware`, không bằng enum.
- Bảng luật: gọi thẳng `awrap_tool_call` với `ToolCallRequest` dựng tay, một
  dòng cho mỗi mã từ chối cộng các dòng "được phép".
- `decide_pending` / `appointment_task`: chạy một `create_agent` nhỏ với model
  `scripted` (không Azure) và Mongo thật.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Optional

import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agents.salon.orchestrator.middleware.tool_rules import ToolRulesMiddleware
from app.agents.salon.orchestrator.state import SalonContext, SalonState
from app.agents.salon.orchestrator.tools import CONTROL_TOOLS, make_orchestrator_tools
from app.agents.salon.shared.context import SnapshotHolder
from app.agents.salon.shared.schemas import AgentStatusFact, ServiceFact
from app.agents.salon.turn.ledger import TurnLedger
from app.core.clock import now_utc
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.appointment import AppointmentService
from app.services.proposal import ProposalService
from app.services.waiting_session import WaitingSessionService
from tests.fakes import scripted, tool_call
from tests.salon.subagents import (
    a_user, default_hours, moment, result, seed_services, tomorrow_at, use_model,
)

THREAD_ID = "thread-1"


class Ledger:
    """`LedgerReader` giả: dịch vụ đã nhận ra ở lượt trước tuỳ test."""

    def __init__(self, known: Optional[list[ServiceFact]] = None) -> None:
        self.known = known or []

    async def service_facts(self, *, thread_id: str):
        return list(self.known)

    async def unannounced(self, user_id: str, *, today):
        return None


def _service_fact(service_id: str = "toc-nhuom") -> ServiceFact:
    return ServiceFact(service_id=service_id, name="Nhuộm", price_min=350000, price_max=700000,
                       duration_minutes=120)


def _status(status: str) -> dict:
    return AgentStatusFact(agent="service", status=status).model_dump(mode="json")


def _context(user, *, asked_at, turn_id="turn-1", question="") -> SalonContext:
    return SalonContext(user_id=str(user.id), turn_id=turn_id, thread_id=THREAD_ID,
                        asked_at=asked_at, question=question, preset=None)


async def _open_session(db, user, *, agent="appointment", asked_minutes_ago=0, memory=None):
    asked = now_utc() - timedelta(minutes=asked_minutes_ago)
    await WaitingSessionService(db).open(WaitingSession(
        user_id=str(user.id), agent=agent, task="Book tomorrow",
        memory=memory if memory is not None else {},
        question=SessionQuestion(field="time", options=[
            SessionOption(**moment(tomorrow_at(16))),
        ], text="16:00?"),
        asked_at=asked, expires_at=asked + timedelta(minutes=10), opened_turn_id="turn-0",
    ))


async def _pending(db, user, *, hour=15, service_ids=None):
    return await ProposalService(db).create(user, "book", tomorrow_at(hour), None,
                                            service_ids=service_ids)


def _tool_messages(state) -> list[ToolMessage]:
    return [m for m in state["messages"] if isinstance(m, ToolMessage)]


def _error_code(message: ToolMessage) -> Optional[str]:
    if message.status != "error":
        return None
    return json.loads(message.content)["error_code"]


# ---------------------------------------------------------------------------
# Schema cố định
# ---------------------------------------------------------------------------


def _schemas(tools) -> list[dict]:
    return [convert_to_openai_tool(tool, strict=True) for tool in tools]


async def test_orchestrator_tool_schemas_do_not_change_between_turns(test_db):
    await default_hours(test_db)
    await seed_services(test_db)
    # Lượt A: khách có lịch sắp tới + đề xuất đang chờ + dịch vụ đã biết.
    busy = await a_user(test_db)
    await AppointmentService(test_db).create(busy, tomorrow_at(10), None, service_ids=["blk-60"])
    await _pending(test_db, busy, service_ids=["toc-nhuom"])
    holder_a = SnapshotHolder(test_db, busy, asked_at=now_utc(), thread_id=THREAD_ID,
                              services=Ledger([_service_fact()]))
    # Lượt B: khách khác, không lịch, không đề xuất.
    quiet = await a_user(test_db, phone="0987654321", name="Chú Ba")
    holder_b = SnapshotHolder(test_db, quiet, asked_at=now_utc(), thread_id=THREAD_ID,
                              services=Ledger())
    snap_a, snap_b = await holder_a.current(), await holder_b.current()
    assert snap_a.pending is not None and snap_b.pending is None
    assert snap_a.upcoming and not snap_b.upcoming

    tools_a = make_orchestrator_tools(test_db, busy, holder_a)
    tools_b = make_orchestrator_tools(test_db, quiet, holder_b)

    dumped_a = json.dumps(_schemas(tools_a), sort_keys=True)
    dumped_b = json.dumps(_schemas(tools_b), sort_keys=True)
    assert dumped_a == dumped_b
    names = {tool.name for tool in tools_a}
    assert names == {"appointment_task", "shop_info_task", "service_task", "decide_pending"}
    assert names <= CONTROL_TOOLS
    for schema in _schemas(tools_a):
        params = schema["function"]["parameters"]
        # Strict-safe: mọi tham số bắt buộc, không giá trị mặc định, không enum id.
        assert set(params["required"]) == set(params["properties"])
        assert "default" not in json.dumps(params)
        assert "toc-nhuom" not in json.dumps(params)


# ---------------------------------------------------------------------------
# Bảng luật — gọi thẳng awrap_tool_call
# ---------------------------------------------------------------------------

_APPT = {"request": "Book 4pm tomorrow", "continue_session": False, "service_ids": None}
_SHOP = {"request": "Is the shop open tomorrow at 9?", "continue_session": False}
_DECIDE = {"label": "approve", "aside": None, "reason": "customer agreed"}

# (id, tool, args, setup, expected error_code hoặc None = được phép)
_RULES = [
    # 1 — tham số sai schema
    ("bad_args_wrong_type", "appointment_task", {**_APPT, "continue_session": "yes"}, {}, "bad_args"),
    ("bad_args_missing_key", "shop_info_task", {"request": "x"}, {}, "bad_args"),
    ("bad_args_extra_key", "decide_pending", {**_DECIDE, "extra": 1}, {"pending": True}, "bad_args"),
    ("bad_args_bad_label", "decide_pending", {**_DECIDE, "label": "yes"}, {"pending": True},
     "bad_args"),
    # 1b — id dịch vụ phải đến từ dữ kiện của code
    ("unknown_service", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]}, {},
     "unknown_service"),
    ("service_from_turn_fact", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"state": {"turn_facts": [_service_fact().model_dump(mode="json")]}}, None),
    ("service_known_earlier", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"known": [_service_fact()]}, None),
    # Id của phiên Dịch vụ qua được 1b, nhưng phiên đó còn chờ → luật 8 chặn.
    ("service_from_service_session", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"session": ("service", 0, {"service_ids": ["toc-nhuom"]})}, "service_unclear"),
    ("service_from_pending", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"pending": True, "pending_services": ["toc-nhuom"], "state": {"pending_decided": True}}, None),
    ("empty_service_ids", "appointment_task", {**_APPT, "service_ids": []}, {}, None),
    # 2 — làm tiếp phiên phải có phiên còn hạn
    ("no_session", "shop_info_task", {**_SHOP, "continue_session": True}, {}, "no_session"),
    ("continue_expired_session", "appointment_task", {**_APPT, "continue_session": True},
     {"session": ("appointment", 11, {})}, "no_session"),
    ("continue_other_agents_session", "shop_info_task", {**_SHOP, "continue_session": True},
     {"session": ("appointment", 0, {})}, "no_session"),
    ("continue_live_session", "appointment_task", {**_APPT, "continue_session": True},
     {"session": ("appointment", 0, {})}, None),
    # 3 — có đề xuất chờ thì phải quyết trước khi giao Đặt lịch
    ("must_decide_pending", "appointment_task", _APPT, {"pending": True}, "must_decide_pending"),
    ("shop_info_while_pending", "shop_info_task", _SHOP, {"pending": True}, None),
    ("appointment_after_decide", "appointment_task", _APPT,
     {"pending": True, "state": {"pending_decided": True}}, None),
    # 4 — decide_pending
    ("no_pending", "decide_pending", _DECIDE, {}, "no_pending"),
    ("already_decided", "decide_pending", _DECIDE,
     {"pending": True, "state": {"pending_decided": True}}, "already_decided"),
    ("decide_allowed", "decide_pending", _DECIDE, {"pending": True}, None),
    ("decide_twice_in_round", "decide_pending", _DECIDE,
     {"pending": True, "round": (["decide_pending", "decide_pending"], 1)}, "already_decided"),
    # 5 — một đề xuất mới mỗi lượt
    ("one_proposal_per_turn", "appointment_task", _APPT, {"state": {"proposals_created": 1}},
     "one_proposal_per_turn"),
    ("shop_info_after_proposal", "shop_info_task", _SHOP, {"state": {"proposals_created": 1}},
     None),
    # 6 — gọi lại agent đã failed tối đa một lần
    ("retry_once_allowed", "shop_info_task", _SHOP, {"state": {"failed_calls": ["shop_info"]}},
     None),
    ("retry_exhausted", "shop_info_task", _SHOP,
     {"state": {"failed_calls": ["shop_info", "shop_info"]}}, "retry_exhausted"),
    ("other_agent_failed_twice", "appointment_task", _APPT,
     {"state": {"failed_calls": ["shop_info", "shop_info"]}}, None),
    # 7 — hai call cùng một agent con trong một vòng
    ("same_agent_twice_in_round", "shop_info_task", _SHOP,
     {"round": (["shop_info_task", "shop_info_task"], 1)}, "same_agent_twice_in_round"),
    ("first_of_two_in_round", "shop_info_task", _SHOP,
     {"round": (["shop_info_task", "shop_info_task"], 0)}, None),
    ("two_agents_in_round", "shop_info_task", _SHOP,
     {"round": (["appointment_task", "shop_info_task"], 1)}, None),
    # 8 — dịch vụ khách nhắc mà chưa rõ: hỏi trước, không đề xuất
    ("service_session_waiting", "appointment_task", _APPT,
     {"session": ("service", 0, {"candidates": ["nail-sua", "blk-45"], "service_ids": []})},
     "service_unclear"),
    ("service_failed_this_turn", "appointment_task", _APPT,
     {"state": {"turn_facts": [_status("failed")]}}, "service_unclear"),
    ("service_need_info_this_turn", "appointment_task", _APPT,
     {"state": {"turn_facts": [_status("need_info")]}}, "service_unclear"),
    ("service_ok_then_appointment", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"state": {"turn_facts": [_service_fact().model_dump(mode="json"), _status("ok")]}}, None),
    ("service_failed_then_ok", "appointment_task", {**_APPT, "service_ids": ["toc-nhuom"]},
     {"state": {"turn_facts": [_status("failed"), _service_fact().model_dump(mode="json"),
                               _status("ok")]}}, None),
    ("shop_info_while_service_waits", "shop_info_task", _SHOP,
     {"session": ("service", 0, {"candidates": [], "service_ids": []})}, None),
    # 8b — cuộc trò chuyện đã có dịch vụ thì lịch phải mang nó
    ("service_known_earlier_no_ids", "appointment_task", _APPT, {"known": [_service_fact()]},
     "service_required"),
    ("service_this_turn_no_ids", "appointment_task", {**_APPT, "service_ids": []},
     {"state": {"turn_facts": [_service_fact().model_dump(mode="json"), _status("ok")]}},
     "service_required"),
    ("service_task_allowed", "service_task", {"request": "Price of dyeing",
                                              "continue_session": False},
     {"known": [_service_fact()]}, None),
]


async def _request(db, user, tool_name, args, setup) -> tuple[ToolCallRequest, SnapshotHolder]:
    await default_hours(db)
    await seed_services(db)
    if setup.get("pending"):
        await _pending(db, user, service_ids=setup.get("pending_services"))
    if "session" in setup:
        agent, minutes_ago, memory = setup["session"]
        await _open_session(db, user, agent=agent, asked_minutes_ago=minutes_ago, memory=memory)
    asked_at = now_utc() + timedelta(seconds=5)
    holder = SnapshotHolder(db, user, asked_at=asked_at, thread_id=THREAD_ID,
                            services=Ledger(setup.get("known")))
    tools = {tool.name: tool for tool in make_orchestrator_tools(db, user, holder)}

    call_id = uuid.uuid4().hex
    names, index = setup.get("round", ([tool_name], 0))
    calls = []
    for position, name in enumerate(names):
        calls.append({"name": name, "args": args if position == index else {},
                      "id": call_id if position == index else uuid.uuid4().hex,
                      "type": "tool_call"})
    state = {"messages": [HumanMessage("x"), AIMessage(content="", tool_calls=calls)],
             **setup.get("state", {})}
    runtime = ToolRuntime(state=state, context=_context(user, asked_at=asked_at), config={},
                          stream_writer=lambda _: None, tool_call_id=call_id, store=None)
    request = ToolCallRequest(tool_call=calls[index], tool=tools[tool_name], state=state,
                              runtime=runtime)
    return request, holder


class Handler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        return ToolMessage(content="ran", tool_call_id=request.tool_call["id"],
                           name=request.tool_call["name"])


@pytest.mark.parametrize("tool_name, args, setup, expected",
                         [row[1:] for row in _RULES], ids=[row[0] for row in _RULES])
async def test_tool_rules_table(test_db, tool_name, args, setup, expected):
    user = await a_user(test_db)
    request, holder = await _request(test_db, user, tool_name, args, setup)
    handler = Handler()

    out = await ToolRulesMiddleware(holder).awrap_tool_call(request, handler)

    if expected is None:
        assert handler.calls == 1
        assert out.content == "ran"
        return
    assert handler.calls == 0
    assert isinstance(out, ToolMessage)
    assert out.status == "error"
    assert out.tool_call_id == request.tool_call["id"]
    assert out.name == tool_name
    payload = json.loads(out.content)
    assert payload["error_code"] == expected
    assert payload["hint"]


async def test_unknown_tool_name_is_left_to_the_tool_node(test_db):
    user = await a_user(test_db)
    holder = SnapshotHolder(test_db, user, asked_at=now_utc(), thread_id=THREAD_ID,
                            services=Ledger())
    call = {"name": "create_appointment", "args": {"x": 1}, "id": "c1", "type": "tool_call"}
    state = {"messages": [AIMessage(content="", tool_calls=[call])]}
    runtime = ToolRuntime(state=state, context=_context(user, asked_at=now_utc()), config={},
                          stream_writer=lambda _: None, tool_call_id="c1", store=None)
    handler = Handler()

    out = await ToolRulesMiddleware(holder).awrap_tool_call(
        ToolCallRequest(tool_call=call, tool=None, state=state, runtime=runtime), handler,
    )

    assert handler.calls == 1
    assert out.content == "ran"


# ---------------------------------------------------------------------------
# Chạy qua create_agent — decide_pending / appointment_task
# ---------------------------------------------------------------------------


async def _orchestrator_turn(db, user, model, *, question, known=None):
    """Một lượt Orchestrator tối giản: tool thật + ToolRulesMiddleware."""
    asked_at = now_utc() + timedelta(seconds=5)
    turn_id = uuid.uuid4().hex
    ledger = TurnLedger(db)
    await ledger.start(turn_id=turn_id, user_id=str(user.id), thread_id=THREAD_ID,
                       asked_at=asked_at, kind="typed")
    holder = SnapshotHolder(db, user, asked_at=asked_at, thread_id=THREAD_ID,
                            services=Ledger(known))
    before = await holder.current()
    agent = create_agent(
        model=model, tools=make_orchestrator_tools(db, user, holder),
        middleware=[ToolRulesMiddleware(holder)], state_schema=SalonState,
        context_schema=SalonContext,
    )
    state = await agent.ainvoke(
        {"messages": [HumanMessage(question)]},
        context=_context(user, asked_at=asked_at, turn_id=turn_id, question=question),
    )
    return state, holder, before, ledger, turn_id


async def test_decide_pending_records_decision_and_blocks_a_second_call(test_db):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _pending(test_db, user)
    model = scripted([
        tool_call("decide_pending", _DECIDE),
        tool_call("decide_pending", _DECIDE),
        AIMessage(content="Dạ em đặt rồi ạ"),
    ])

    state, holder, before, ledger, turn_id = await _orchestrator_turn(
        test_db, user, model, question="ừ đồng ý")

    assert before.pending is not None
    assert await test_db["appointments"].count_documents({"status": "booked"}) == 1
    first, second = _tool_messages(state)
    assert first.status != "error"
    assert json.loads(first.content)["outcome"] == "approved"
    assert _error_code(second) == "already_decided"

    assert state["pending_decided"] is True
    assert state["pending_outcome"]["outcome"] == "approved"
    assert state["pending_outcome"]["proposal_id"] == before.pending.id
    assert state["decide_calls"] == 1
    assert state["turn_facts"] == [{
        "kind": "decision", "outcome": "approved", "proposal_kind": "book",
        "at": before.pending.at.model_dump(), "error": None,
    }]
    record = await ledger.record(turn_id)
    assert record.decision is not None and record.decision.outcome == "approved"
    # Bối cảnh đọc lại sau khi DB đổi — không còn đề xuất chờ.
    assert (await holder.current()).pending is None


@pytest.mark.parametrize("question, next_requests, outcome", [
    # Khách nêu giờ khác → veto hạ approve thành change → đề xuất bị thay.
    ("ừ mà thôi đổi qua 5 giờ chiều đi", [], "superseded"),
    # Giờ thứ hai là yêu cầu kế đang giữ trong phiên → không tính là đổi ý.
    ("ừ 3 giờ chiều đồng ý, còn 5 giờ chiều cho con gái", [17], "approved"),
])
async def test_decide_pending_runs_veto_with_next_requests_protected(
        test_db, question, next_requests, outcome):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _pending(test_db, user, hour=15)
    await _open_session(test_db, user, memory={
        "next_requests": [moment(tomorrow_at(hour)) for hour in next_requests],
    })
    model = scripted([tool_call("decide_pending", _DECIDE), AIMessage(content="Dạ")])

    state, *_ = await _orchestrator_turn(test_db, user, model, question=question)

    assert state["pending_outcome"]["outcome"] == outcome
    assert [fact["outcome"] for fact in state["turn_facts"]] == [outcome]


async def test_decide_pending_refused_by_veto_records_nothing(test_db):
    # `change` mà khách chỉ nhắc lại đúng giờ đề xuất → veto từ chối; không ghi gì.
    await default_hours(test_db)
    user = await a_user(test_db)
    proposal = await _pending(test_db, user, hour=15)
    model = scripted([tool_call("decide_pending", {**_DECIDE, "label": "change"}),
                      AIMessage(content="Dạ")])

    state, _, _, ledger, turn_id = await _orchestrator_turn(
        test_db, user, model, question="ừ 3 giờ chiều đó")

    [refused] = _tool_messages(state)
    assert _error_code(refused) == "same_time_as_pending"
    assert not state.get("pending_decided")
    assert state.get("pending_outcome") is None
    assert (await ProposalService(test_db).get(user, str(proposal.id))).status == "pending"
    assert (await ledger.record(turn_id)).decision is None


async def test_appointment_task_is_allowed_after_decide(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    await _pending(test_db, user)
    use_model(monkeypatch, scripted([result(status="ok")]))
    model = scripted([
        tool_call("appointment_task", _APPT),
        tool_call("decide_pending", {**_DECIDE, "label": "reject"}),
        tool_call("appointment_task", _APPT),
        AIMessage(content="Dạ"),
    ])

    state, *_ = await _orchestrator_turn(test_db, user, model, question="thôi, đặt 4 giờ chiều mai")

    refused, decided, allowed = _tool_messages(state)
    assert _error_code(refused) == "must_decide_pending"
    assert json.loads(decided.content)["outcome"] == "rejected"
    assert allowed.status != "error"
    assert json.loads(allowed.content)["agent"] == "appointment"
    assert state["pending_outcome"]["outcome"] == "rejected"
    assert state["turn_facts"][-1] == {"kind": "agent_status", "agent": "appointment",
                                       "status": "ok"}
