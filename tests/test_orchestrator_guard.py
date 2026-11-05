"""Task 3.3: `orchestrator/guard.py` — `OrchestratorGuard`, luật (a)–(k) của
§9.3, marker/trùng/ép dừng của §5.6 và trần vòng của §7.1.

Phần lớn test gọi thẳng `awrap_tool_call` với `request` giả
(`SimpleNamespace(tool_call, tool, state, runtime)`) và `handler` giả ghi lại
lời gọi. Các test "end-to-end" dựng một `create_agent` thật với model kịch
bản (`tests/fakes.py`) — không gọi Azure.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.booking.context import AppointmentRef, PendingProposal, TurnContext
from app.agents.booking.orchestrator.guard import (
    REJECT_HINTS,
    OrchestratorGuard,
    is_duplicate,
    markers_for,
    normalize_args,
    rule_e,
    rule_g,
)
from app.agents.booking.orchestrator.state import STREAK_RESET
from app.agents.booking.orchestrator.tools import OrchestratorDeps, make_orchestrator_tools
from app.agents.booking.schemas import (
    AppointmentList,
    AppointmentRefOut,
    FreeSlots,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
)
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.models.conversation import QueuedRequest
from app.services.auth import AuthService
from app.services.proposal import PROPOSAL_MAX_AGE_MINUTES, ProposalService
from tests.factories import make_ctx
from tests.fakes import scripted

CUSTOMER_WORDS = "chị muốn làm móng lúc ba giờ chiều mai nha em"


# ---------------------------------------------------------------------------
# Dựng request/handler/deps giả
# ---------------------------------------------------------------------------


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    """Mốc giờ CỐ ĐỊNH (mai, `hour`:`minute`) — không lệ thuộc giờ chạy test."""
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


class FakeLedger:
    def __init__(self, order: list | None = None) -> None:
        self.appended: list = []
        self.outcomes: list = []
        self.order = order

    async def append_call(self, turn_id, call) -> None:
        self.appended.append(call)
        if self.order is not None:
            self.order.append(("ledger", call.tool, call.action))

    async def set_pending_outcome(self, turn_id, outcome) -> None:
        self.outcomes.append(outcome)


def _unused(*_a, **_kw):
    raise AssertionError("specialist không nên được gọi trong test này")


def make_deps(**overrides) -> OrchestratorDeps:
    defaults = dict(
        db=None,
        user=SimpleNamespace(id="u-1"),
        ctx=make_ctx(),
        ledger=FakeLedger(),
        turn_id="turn-1",
        question=CUSTOMER_WORDS,
        max_rounds=8,
        run_appointment=_unused,
        run_shop=_unused,
    )
    defaults.update(overrides)
    return OrchestratorDeps(**defaults)


def make_guard(deps, **kw) -> OrchestratorGuard:
    kw.setdefault("max_calls_per_round", 3)
    kw.setdefault("no_progress_limit", 2)
    return OrchestratorGuard(deps, **kw)


def _time(**kw) -> dict:
    base = dict(
        day=None, weekday=None, day_offset=None, hour=None, minute=None, part_of_day=None,
        ambiguous_week=None, is_now=None, anchor=None, queue_id=None,
    )
    base.update(kw)
    return base


def _appt(action: str, **kw) -> dict:
    args = dict(time=None, service=None, appointment_id=None, for_whom=None, also=None)
    args.update(kw)
    return {"action": action, "args": args}


def _shop(action: str, time=None) -> dict:
    return {"action": action, "args": {"time": time}}


def _decide(label="approve", aside=None, reason="khach noi ok") -> dict:
    return {"label": label, "aside": aside, "reason": reason}


def _finish(stated=None) -> dict:
    return {"kind_hint": "answer", "service": None, "stated": stated or []}


def call(name: str, args: dict, id: str | None = None) -> dict:
    return {"name": name, "args": args, "id": id or uuid.uuid4().hex, "type": "tool_call"}


def state_for(*calls, **extra) -> dict:
    return {"messages": [HumanMessage("x"), AIMessage(content="", tool_calls=list(calls))], **extra}


def request_for(c: dict, state: dict, tools: dict | None = None):
    return SimpleNamespace(
        tool_call=c,
        tool=(tools or {}).get(c["name"]),
        state=state,
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "thread-1"}}),
    )


class RecordingHandler:
    """Handler giả: ghi lại request và trả một ToolMessage với `content` dựng sẵn."""

    def __init__(self, content: str = '{"ok": true}') -> None:
        self.content = content
        self.requests: list = []

    async def __call__(self, req):
        self.requests.append(req)
        return ToolMessage(content=self.content, tool_call_id=req.tool_call["id"], name=req.tool_call["name"])


async def tool_handler(req):
    """Như `ToolNode`: chạy tool thật, để exception (DecideRejected) văng lên."""
    return await req.tool.ainvoke({**req.tool_call, "type": "tool_call"})


def error_code(cmd) -> str | None:
    msg = cmd.update["messages"][0]
    if msg.status != "error":
        return None
    return json.loads(msg.content)["error_code"]


def _pending(**overrides) -> PendingProposal:
    start = _tomorrow_at(15)
    defaults = dict(
        id="p1", kind="book", start_at=start, spoken=format_vi_datetime(start), note=None,
        expires_at=now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES), unclear_count=0, target=None,
    )
    defaults.update(overrides)
    return PendingProposal(**defaults)


def _outcome_json(outcome: str) -> str:
    return PendingOutcome(outcome=outcome, proposal_id="p1", kind="book", spoken="3 giờ chiều mai").model_dump_json()


def _record(tool, action, args_norm, *, allowed=True, markers=(), reject_code=None) -> dict:
    return {
        "round": 1, "index": 0, "tool": tool, "action": action, "args_norm": args_norm,
        "allowed": allowed, "reject_code": reject_code, "result": None, "markers": list(markers),
        "at": now_utc().isoformat(),
    }


# ---------------------------------------------------------------------------
# (a)–(k)
# ---------------------------------------------------------------------------


async def test_a_unknown_tool_is_rejected():
    deps = make_deps()
    c = call("book_now", {"x": 1})
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) == "unknown_tool"
    assert handler.requests == []
    assert [r.allowed for r in deps.ledger.appended] == [False]


@pytest.mark.parametrize(
    "name,args",
    [
        ("appointment_task", _appt("resolve_time", time=_time(hour=25))),
        ("appointment_task", _appt("resolve_time", time=_time(minute=60))),
        ("appointment_task", _appt("find_slots", time=_time(weekday=7))),
        ("appointment_task", _appt("find_slots", time=_time(day_offset=91))),
        ("appointment_task", _appt("find_slots", time=_time(day_offset=-2))),
        ("appointment_task", _appt("propose_book", time=_time(hour=9),
                                   also=[{"time": _time(hour=30), "service": None, "for_whom": None}])),
        ("shop_info_task", _shop("is_open_at", _time(hour=-1))),
        ("finish", _finish(stated=["a", "b", "c"])),
        ("appointment_task", {"action": "resolve_time"}),  # thiếu `args`
        ("ask_customer", {"field": "color", "hint": None}),
    ],
    ids=["hour", "minute", "weekday", "day_offset_high", "day_offset_low", "also_hour", "shop_hour",
         "stated", "missing_args", "bad_literal"],
)
async def test_b_hour_out_of_range_is_rejected_without_running(name, args):
    deps = make_deps()
    c = call(name, args)
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) == "bad_args"
    assert handler.requests == []


@pytest.mark.parametrize(
    "args,code",
    [
        (_appt("propose_cancel", appointment_id="not-yours"), "unknown_appointment_id"),
        (_appt("resolve_time", time=_time(anchor="queued", queue_id="q-x")), "unknown_queue_id"),
        (_appt("propose_book", time=_time(hour=9),
               also=[{"time": _time(anchor="queued", queue_id="q-x"), "service": None, "for_whom": None}]),
         "unknown_queue_id"),
    ],
)
async def test_c_unknown_appointment_id_is_rejected(args, code):
    start = _tomorrow_at(10)
    ctx = make_ctx(
        upcoming=[AppointmentRef(id="a-1", start_at=start, spoken=format_vi_datetime(start))],
        queued_requests=[QueuedRequest(queue_id="q-1", start_at=start, spoken=format_vi_datetime(start), queued_at=now_utc())],
    )
    deps = make_deps(ctx=ctx)
    c = call("appointment_task", args)
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) == code
    assert handler.requests == []


async def test_d_anchor_pending_without_pending_is_rejected():
    deps = make_deps(ctx=make_ctx(pending=None))
    c = call("appointment_task", _appt("resolve_time", time=_time(hour=16, anchor="pending")))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) == "missing_anchor_target"
    assert handler.requests == []


async def test_e_second_propose_in_the_turn_is_rejected():
    deps = make_deps()
    c = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c, proposals_created=1)), handler)

    assert error_code(cmd) == "one_proposal_per_turn"
    assert handler.requests == []


async def test_e_sibling_propose_with_lower_index_blocks_this_one():
    """End-to-end qua `awrap_tool_call`: (g) chạy trước (e), nên đề xuất anh
    em index lớn hơn bị chặn với `serialize_required` (spec §13: "2
    propose_book một AIMessage → call index 1 serialize_required"). Mệnh đề
    "anh em" của (e) là phòng thủ chiều sâu — kiểm riêng ở
    `test_e_rule_blocks_a_propose_with_a_lower_index_propose_sibling`."""
    deps = make_deps()
    first = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))
    second = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=10)))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(second, state_for(first, second)), handler)

    assert error_code(cmd) == "serialize_required"
    assert handler.requests == []


def test_e_rule_blocks_a_propose_with_a_lower_index_propose_sibling():
    first = call("appointment_task", _appt("propose_book", time=_time(hour=9)))
    second = call("appointment_task", _appt("propose_move", time=_time(hour=10)))
    other = call("appointment_task", _appt("find_slots", time=_time(day_offset=1)))

    assert rule_e(second, [first, second], proposals_created=0) == "one_proposal_per_turn"
    assert rule_e(first, [first, second], proposals_created=0) is None
    assert rule_e(first, [other, first], proposals_created=0) is None
    assert rule_e(first, [first], proposals_created=1) == "one_proposal_per_turn"


async def test_f_duplicate_call_without_new_marker_is_rejected_and_bumps_streak():
    deps = make_deps()
    args = _appt("resolve_time", time=_time(day_offset=1, hour=15))
    c = call("appointment_task", args)
    previous = _record("appointment_task", "resolve_time", normalize_args("appointment_task", args),
                       markers=["time_resolved"])
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c, calls=[previous])), handler)

    assert error_code(cmd) == "duplicate_call"
    assert handler.requests == []
    assert cmd.update["no_progress_streak"] == 1
    assert not cmd.update.get("force_end")


@pytest.mark.parametrize(
    "calls,expected",
    [
        ([], False),
        ([_record("appointment_task", "resolve_time", "K")], True),
        # Lần trước bị TỪ CHỐI → lặp lại là sửa sai, không phải trùng.
        ([_record("appointment_task", "resolve_time", "K", allowed=False, reject_code="serialize_required")], False),
        # Có marker mới SAU lần trước → không trùng.
        ([_record("appointment_task", "resolve_time", "K"),
          _record("appointment_task", "find_slots", "Z", markers=["slots_found:2026-11-06"])], False),
        # Khoá khác (action khác) → không trùng.
        ([_record("appointment_task", "find_slots", "K")], False),
    ],
    ids=["empty", "same_key", "previous_rejected", "marker_since", "other_action"],
)
def test_is_duplicate_table(calls, expected):
    assert is_duplicate(calls, "appointment_task", "resolve_time", "K") is expected


async def test_g_finish_next_to_another_call_is_serialize_required():
    deps = make_deps()
    other = call("shop_info_task", _shop("get_hours"))
    fin = call("finish", _finish())
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(fin, state_for(other, fin)), handler)

    assert error_code(cmd) == "serialize_required"
    assert handler.requests == []


async def test_g_lone_lowest_index_sequential_call_runs():
    """D′: một `propose_*` không có anh em NHÓM TUẦN TỰ nào index nhỏ hơn
    vẫn chạy dù cùng vòng với một call ngoài nhóm."""
    deps = make_deps()
    propose = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))
    other = call("shop_info_task", _shop("get_hours"))
    result = TaskResult(task_id="t0", agent="appointment", action="propose_book", status="needs_info")
    handler = RecordingHandler(result.model_dump_json())

    cmd = await make_guard(deps).awrap_tool_call(request_for(propose, state_for(propose, other)), handler)

    assert error_code(cmd) is None
    assert len(handler.requests) == 1


_P0 = call("appointment_task", _appt("propose_book", time=_time(hour=9)), id="p0")
_P1 = call("appointment_task", _appt("propose_book", time=_time(hour=10)), id="p1")
_D = call("decide_pending", _decide(), id="d")
_F = call("finish", _finish(), id="f")
_A = call("ask_customer", {"field": "day", "hint": None}, id="a")
_S = call("shop_info_task", _shop("get_hours"), id="s")
_R = call("appointment_task", _appt("resolve_time", time=_time(hour=9)), id="r")


@pytest.mark.parametrize(
    "this,sibs,expected",
    [
        (_F, [_F], None),                          # một mình → chạy
        (_A, [_A], None),
        (_F, [_S, _F], "serialize_required"),      # finish/ask luôn phải đi một mình
        (_F, [_F, _S], "serialize_required"),
        (_A, [_R, _A], "serialize_required"),
        (_P0, [_P0, _P1], None),                   # index nhỏ nhất của nhóm → chạy
        (_P1, [_P0, _P1], "serialize_required"),
        (_P0, [_S, _P0], None),                    # anh em ngoài nhóm không chặn
        (_D, [_D, _R], None),
        (_P0, [_D, _P0], "serialize_required"),    # decide trước, propose sau
        (_D, [_P0, _D], "serialize_required"),
        (_S, [_P0, _S], None),                     # call ngoài nhóm không bị (g)
    ],
)
def test_g_rule_table(this, sibs, expected):
    assert rule_g(this, sibs) == expected


async def a_user(db, name="Cô Lan"):
    phone = f"09{uuid.uuid4().int % 100000000:08d}"
    return await AuthService(db).create_user(phone, "matkhau123", name)


async def test_h_propose_without_decide_is_rejected_and_pending_untouched(test_db, clean_db):
    user = await a_user(test_db)
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    pending = PendingProposal(
        id=str(proposal.id), kind="book", start_at=proposal.start_at, spoken=format_vi_datetime(start),
        note=None, expires_at=proposal.expires_at, unclear_count=0, target=None,
    )
    deps = make_deps(db=test_db, user=user, ctx=make_ctx(pending=pending))
    c = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=15)))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) == "must_decide_pending"
    assert handler.requests == []
    assert cmd.update["no_progress_streak"] == 1
    latest = await ProposalService(test_db).latest(user)
    assert str(latest.id) == str(proposal.id)
    assert latest.status == "pending"


async def test_h_two_rejections_in_a_row_record_kept():
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    first = call("finish", _finish())
    record = _record("finish", None, normalize_args("finish", first["args"]), allowed=False,
                     reject_code="must_decide_pending")
    second = call("finish", _finish())
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(
        request_for(second, state_for(second, calls=[record], no_progress_streak=1)), handler
    )

    assert error_code(cmd) == "must_decide_pending"
    assert handler.requests == []
    assert cmd.update["pending_decided"] is True
    assert cmd.update["pending_outcome"]["outcome"] == "kept"
    assert [o.outcome for o in deps.ledger.outcomes] == ["kept"]


async def test_i_veto_result_flows_through_decide_pending():
    pending = _pending()
    deps = make_deps(ctx=make_ctx(pending=pending), question=f"đổi qua {pending.spoken} nha")
    tools = {t.name: t for t in make_orchestrator_tools(deps)}
    c = call("decide_pending", _decide(label="change"))

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c), tools), tool_handler)

    assert error_code(cmd) == "same_time_as_pending"
    assert "pending_decided" not in cmd.update
    assert [r.reject_code for r in deps.ledger.appended] == ["same_time_as_pending"]


async def test_decide_pending_without_pending_is_rejected_no_pending():
    deps = make_deps(ctx=make_ctx(pending=None))
    tools = {t.name: t for t in make_orchestrator_tools(deps)}
    c = call("decide_pending", _decide())

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c), tools), tool_handler)

    assert error_code(cmd) == "no_pending"


async def test_j_propose_after_approved_is_rejected():
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    c = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))
    state = state_for(c, pending_decided=True, pending_outcome=json.loads(_outcome_json("approved")))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state), handler)

    assert error_code(cmd) == "already_approved"
    assert handler.requests == []


async def test_k_second_decide_pending_is_already_decided():
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    c = call("decide_pending", _decide(label="reject"))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c, pending_decided=True)), handler)

    assert error_code(cmd) == "already_decided"
    assert handler.requests == []


async def test_too_many_calls_fourth_call_is_rejected():
    deps = make_deps()
    calls = [call("appointment_task", _appt("find_slots", time=_time(day_offset=i))) for i in range(4)]
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(request_for(calls[3], state_for(*calls)), handler)

    assert error_code(cmd) == "too_many_calls"
    assert handler.requests == []
    assert deps.ledger.appended[0].index == 3


# ---------------------------------------------------------------------------
# Kết quả được phép: state + ép dừng
# ---------------------------------------------------------------------------


async def test_unclear_first_time_sets_force_end():
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    c = call("decide_pending", _decide(label="unclear"))
    handler = RecordingHandler(_outcome_json("clarify"))

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), handler)

    assert error_code(cmd) is None
    assert cmd.update["force_end"] is True
    assert cmd.update["pending_decided"] is True
    assert cmd.update["progress_markers"] == ["pending_decided:clarify"]


async def test_allowed_task_writes_markers_proposal_count_and_resets_streak():
    deps = make_deps()
    c = call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))
    created = ProposalCreated(
        proposal_id="p9", kind="book", start_at=Moment(iso=_tomorrow_at(9).isoformat(), spoken="9 giờ sáng mai"),
        note=None, replaces=None, queued=[],
    )
    result = TaskResult(task_id="t0", agent="appointment", action="propose_book", status="ok", data=created)
    handler = RecordingHandler(result.model_dump_json())

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c, no_progress_streak=1)), handler)

    assert cmd.update["proposals_created"] == 1
    assert cmd.update["progress_markers"] == ["proposal_created:p9"]
    assert cmd.update["no_progress_streak"] == STREAK_RESET
    [record] = deps.ledger.appended
    assert record.allowed and record.markers == ["proposal_created:p9"]


@pytest.mark.parametrize(
    "name,args,field",
    [("finish", _finish(stated=["gel"]), "finish_args"), ("ask_customer", {"field": "day", "hint": None}, "ask_args")],
)
async def test_ending_tools_record_ended_by_and_their_args(name, args, field):
    deps = make_deps()
    c = call(name, args)

    cmd = await make_guard(deps).awrap_tool_call(request_for(c, state_for(c)), RecordingHandler())

    assert cmd.update["ended_by"] == name
    assert cmd.update[field] == args


async def test_streak_of_two_sets_force_end():
    deps = make_deps()
    args = _appt("find_slots", time=_time(day_offset=1))
    c = call("appointment_task", args)
    previous = _record("appointment_task", "find_slots", normalize_args("appointment_task", args))
    handler = RecordingHandler()

    cmd = await make_guard(deps).awrap_tool_call(
        request_for(c, state_for(c, calls=[previous], no_progress_streak=1)), handler
    )

    assert error_code(cmd) == "duplicate_call"
    assert cmd.update["force_end"] is True


# ---------------------------------------------------------------------------
# abefore_model: ép dừng, vòng toàn từ chối, trần vòng
# ---------------------------------------------------------------------------


def _error_tm(c: dict, code: str) -> ToolMessage:
    return ToolMessage(content=json.dumps({"error_code": code, "hint": "x"}), tool_call_id=c["id"],
                       name=c["name"], status="error")


async def test_before_model_jumps_to_end_on_force_end_with_ended_by_forced():
    guard = make_guard(make_deps())

    update = await guard.abefore_model({"messages": [HumanMessage("x")], "force_end": True}, None)

    assert update["jump_to"] == "end"
    assert update["ended_by"] == "forced"


async def test_all_rejected_round_bumps_streak_once_in_before_model():
    guard = make_guard(make_deps())
    a, b = call("finish", _finish()), call("ask_customer", {"field": "day", "hint": None})
    msgs = [HumanMessage("x"), AIMessage(content="", tool_calls=[a, b]),
            _error_tm(a, "serialize_required"), _error_tm(b, "serialize_required")]

    first = await guard.abefore_model({"messages": msgs, "no_progress_streak": 0}, None)
    assert first == {"no_progress_streak": 1}

    second = await guard.abefore_model({"messages": msgs, "no_progress_streak": 1}, None)
    assert second["jump_to"] == "end" and second["ended_by"] == "forced"


async def test_all_rejected_round_already_bumped_by_a_duplicate_is_not_counted_twice():
    guard = make_guard(make_deps())
    a = call("appointment_task", _appt("find_slots", time=_time(day_offset=1)))
    msgs = [HumanMessage("x"), AIMessage(content="", tool_calls=[a]), _error_tm(a, "duplicate_call")]

    update = await guard.abefore_model({"messages": msgs, "no_progress_streak": 1}, None)

    assert not update or "jump_to" not in update


# ---------------------------------------------------------------------------
# End-to-end với create_agent thật + model kịch bản
# ---------------------------------------------------------------------------


def _find_slots_runner(order: list | None = None):
    async def run(spec, ctx, question):
        offset = spec.args.time.day_offset if spec.args.time else 0
        day = (to_local(now_utc()) + timedelta(days=offset or 0)).date()
        if spec.action == "propose_book":
            data = ProposalCreated(
                proposal_id="p-new", kind="book",
                start_at=Moment(iso=_tomorrow_at(9).isoformat(), spoken="9 giờ sáng mai"),
                note=None, replaces=None, queued=[],
            )
        elif spec.action == "resolve_time":
            data = ResolvedTime(start_at=Moment(iso=_tomorrow_at(9).isoformat(), spoken="9 giờ sáng mai"),
                                partial_date=None, missing=[], day_from=None)
        else:
            data = FreeSlots(day=day, slots=[], more=False, fetched_at=now_utc())
        return TaskResult(task_id=spec.task_id, agent="appointment", action=spec.action, status="ok", data=data)

    return run


async def _shop_hours(spec, ctx, question):
    open_, close = _tomorrow_at(8), _tomorrow_at(19)
    data = ShopHoursOut(open=Moment(iso=open_.isoformat(), spoken="8 giờ"),
                        close=Moment(iso=close.isoformat(), spoken="19 giờ"), closed_days=[])
    return TaskResult(task_id=spec.task_id, agent="shop_info", action=spec.action, status="ok", data=data)


def _agent(deps, replies, **guard_kw):
    model = scripted(replies, tags=("orchestrate",))
    agent = create_agent(model, tools=make_orchestrator_tools(deps), middleware=[make_guard(deps, **guard_kw)])
    return agent, model


def _ai(*calls) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


async def test_parallel_same_round_updates_do_not_raise():
    deps = make_deps(run_appointment=_find_slots_runner(), run_shop=_shop_hours)
    agent, _ = _agent(deps, [
        _ai(call("appointment_task", _appt("resolve_time", time=_time(day_offset=1, hour=9))),
            call("appointment_task", _appt("find_slots", time=_time(day_offset=1))),
            call("shop_info_task", _shop("get_hours"))),
        _ai(call("finish", _finish())),
    ])

    out = await agent.ainvoke({"messages": [HumanMessage("x")]})

    assert out["ended_by"] == "finish"
    assert len(out["calls"]) == 4
    assert all(c["allowed"] for c in out["calls"])
    assert len(deps.ledger.appended) == 4
    assert out["rounds_executed"] == 1


async def test_proposal_record_is_in_the_ledger_before_the_next_model_call():
    order: list = []
    deps = make_deps(run_appointment=_find_slots_runner(), ledger=FakeLedger(order))

    class OrderedModel(type(scripted([]))):
        async def _anext(self, messages):
            order.append(("model",))
            return await super()._anext(messages)

    model = OrderedModel(replies=[
        _ai(call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9)))),
        _ai(call("finish", _finish())),
    ], calls=[], bind_kwargs=[], tags=["orchestrate"])
    agent = create_agent(model, tools=make_orchestrator_tools(deps), middleware=[make_guard(deps)])

    await agent.ainvoke({"messages": [HumanMessage("x")]})

    assert order == [
        ("model",),
        ("ledger", "appointment_task", "propose_book"),
        ("model",),
        ("ledger", "finish", None),
    ]
    proposal_records = [r for r in deps.ledger.appended if r.action == "propose_book"]
    assert len(proposal_records) == 1
    assert isinstance(proposal_records[0].result.data, ProposalCreated)


async def test_rounds_with_only_rejections_do_not_count_toward_the_limit():
    """3 vòng toàn bị từ chối xen giữa 8 vòng chạy được (mỗi vòng một ngày
    khác → marker mới, streak về 0) → dừng `limit` ngay sau vòng chạy thứ 8;
    lời đáp thứ 12 của model không bao giờ được dùng."""
    deps = make_deps(run_appointment=_find_slots_runner())
    run = [_ai(call("appointment_task", _appt("find_slots", time=_time(day_offset=i)))) for i in range(1, 9)]
    rej = [_ai(call("book_now", {"n": i})) for i in range(3)]
    replies = [rej[0], run[0], rej[1], run[1], rej[2], *run[2:], _ai(call("finish", _finish()))]
    agent, model = _agent(deps, replies)

    out = await agent.ainvoke({"messages": [HumanMessage("x")]}, {"recursion_limit": 200})

    assert out["ended_by"] == "limit"
    assert out["rounds_executed"] == 8
    assert len(model.replies) == 1


async def test_repeated_serialize_required_round_is_forced_to_end():
    deps = make_deps()
    pair = lambda: _ai(call("finish", _finish()), call("ask_customer", {"field": "day", "hint": None}))  # noqa: E731
    agent, model = _agent(deps, [pair(), pair(), _ai(call("finish", _finish()))])

    out = await agent.ainvoke({"messages": [HumanMessage("x")]})

    assert out["ended_by"] == "forced"
    assert len(model.replies) == 1


# ---------------------------------------------------------------------------
# Marker, hint, log
# ---------------------------------------------------------------------------

_M = Moment(iso="2026-11-06T09:00:00+07:00", spoken="9 giờ sáng mai")


def _tr(action, data, status="ok"):
    return TaskResult(task_id="t", agent="appointment", action=action, status=status, data=data)


@pytest.mark.parametrize(
    "tool,action,result,expected",
    [
        ("appointment_task", "resolve_time",
         _tr("resolve_time", ResolvedTime(start_at=_M, partial_date=None, missing=[], day_from=None)), ["time_resolved"]),
        ("appointment_task", "resolve_time",
         _tr("resolve_time", ResolvedTime(start_at=None, partial_date=None, missing=["giờ cụ thể"], day_from=None),
             status="needs_info"), []),
        ("appointment_task", "find_slots",
         _tr("find_slots", FreeSlots(day="2026-11-06", slots=[_M], more=False, fetched_at=now_utc())),
         ["slots_found:2026-11-06"]),
        ("appointment_task", "propose_book",
         _tr("propose_book", ProposalCreated(proposal_id="p7", kind="book", start_at=_M, note=None, replaces=None,
                                             queued=[])), ["proposal_created:p7"]),
        ("appointment_task", "list_appointments",
         _tr("list_appointments", AppointmentList(appointments=[AppointmentRefOut(id="a", at=_M)])),
         ["appointments_listed"]),
        ("shop_info_task", "get_hours",
         _tr("get_hours", ShopHoursOut(open=_M, close=_M, closed_days=[])), ["shop_hours_known"]),
        ("shop_info_task", "get_status",
         _tr("get_status", ShopStatusOut(busy=False, until=None)), ["shop_status_known"]),
        ("shop_info_task", "is_open_at",
         _tr("is_open_at", ShopOpenOut(at=_M, open=True, reason="open", next_change=None)),
         [f"shop_open_checked:{_M.iso}"]),
        ("appointment_task", "propose_book", _tr("propose_book", None, status="failed"), []),
        ("decide_pending", None,
         PendingOutcome(outcome="approved", proposal_id="p1", kind="book", spoken="x"), ["pending_decided:approved"]),
        ("finish", None, None, []),
    ],
)
def test_markers_for_each_result_kind(tool, action, result, expected):
    assert markers_for(tool, action, result) == expected


REQUIRED_CODES = {
    "unknown_tool", "bad_args", "unknown_appointment_id", "unknown_queue_id", "missing_anchor_target",
    "one_proposal_per_turn", "duplicate_call", "serialize_required", "must_decide_pending",
    "same_time_as_pending", "already_approved", "already_decided", "too_many_calls", "no_pending",
}


def test_every_reject_code_has_an_english_hint():
    assert REQUIRED_CODES <= set(REJECT_HINTS)
    for code, hint in REJECT_HINTS.items():
        assert hint.strip(), code
        assert hint.isascii(), code  # tiếng Anh, không dấu tiếng Việt


async def test_every_call_logs_orch_call_with_round_index_tool_allowed_and_reject_code():
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    guard = make_guard(deps)
    decide = call("decide_pending", _decide(label="none", reason=CUSTOMER_WORDS))
    bad = call("book_now", {"q": 1})

    with structlog.testing.capture_logs() as logs:
        await guard.awrap_tool_call(request_for(decide, state_for(decide)), RecordingHandler(_outcome_json("kept")))
        await guard.awrap_tool_call(request_for(bad, state_for(bad)), RecordingHandler())

    orch = [e for e in logs if e["event"] == "orch_call"]
    assert len(orch) == 2
    for entry in orch:
        extra = entry["extra"]
        assert {"user_id", "turn_id", "thread_id", "round", "call_index", "tool", "action", "args_norm",
                "allowed", "reject_code"} <= set(extra)
        assert extra["thread_id"] == "thread-1" and extra["round"] == 1 and extra["call_index"] == 0
    assert [(e["extra"]["tool"], e["extra"]["allowed"], e["extra"]["reject_code"]) for e in orch] == [
        ("decide_pending", True, None), ("book_now", False, "unknown_tool"),
    ]
    decided = [e for e in logs if e["event"] == "pending_decided"]
    assert len(decided) == 1 and decided[0]["extra"]["outcome"] == "kept"
    # Không bao giờ log chữ khách (câu hỏi, hay `reason` model trích từ câu khách).
    assert CUSTOMER_WORDS not in json.dumps(logs, default=str, ensure_ascii=False)


async def test_rejected_lone_finish_goes_back_to_the_model():
    """Ruling Task 3.3: `finish` không `return_direct` — một `finish` bị (h)
    từ chối KHÔNG kết thúc lượt; model được gọi lại, quyết đề xuất, rồi mới
    `finish` (được phép) kết thúc."""
    deps = make_deps(ctx=make_ctx(pending=_pending()))
    agent, model = _agent(deps, [
        _ai(call("finish", _finish())),
        _ai(call("decide_pending", _decide(label="none"))),
        _ai(call("finish", _finish())),
    ])

    out = await agent.ainvoke({"messages": [HumanMessage("x")]})

    assert [(c["tool"], c["reject_code"]) for c in out["calls"]] == [
        ("finish", "must_decide_pending"), ("decide_pending", None), ("finish", None),
    ]
    assert out["ended_by"] == "finish"
    assert model.replies == []


@pytest.mark.parametrize(
    "name,args", [("finish", _finish()), ("ask_customer", {"field": "day", "hint": None})]
)
async def test_allowed_lone_ending_call_ends_the_run_without_another_model_call(name, args):
    deps = make_deps()
    agent, model = _agent(deps, [_ai(call(name, args)), _ai(call("finish", _finish()))])

    out = await agent.ainvoke({"messages": [HumanMessage("x")]})

    assert out["ended_by"] == name
    assert len(model.replies) == 1


async def test_before_model_keeps_ended_by_from_the_guard():
    guard = make_guard(make_deps())

    update = await guard.abefore_model({"messages": [HumanMessage("x")], "ended_by": "ask_customer"}, None)

    assert update == {"jump_to": "end"}
