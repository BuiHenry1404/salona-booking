"""Task 3.1: `orchestrator/state.py` + `orchestrator/tools.py` — 5 tool điều
khiển của Orchestrator và `OrchestratorDeps` (§5.1, §5.2 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Không gọi Azure thật: `run_appointment`/`run_shop` luôn là hàm giả ở đây —
hành vi thật của hai specialist đã có test riêng (Task 2.4/2.5).
"""
from __future__ import annotations

import json
import re
import typing
from datetime import timedelta

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agents.booking.context import AppointmentRef
from app.agents.booking.orchestrator.tools import OrchestratorDeps, make_orchestrator_tools
from app.agents.booking.schemas import AppointmentArgs, Moment, ProposalCreated, TaskResult, TaskSpec
from app.core.clock import now_utc
from app.models.conversation import QueuedRequest
from tests.factories import make_ctx

EXPECTED_NAMES = {"appointment_task", "shop_info_task", "decide_pending", "ask_customer", "finish"}

_VI_DIACRITICS = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.I
)


class FakeLedger:
    """Ghi lại mọi `append_call` — không chạm Mongo."""

    def __init__(self) -> None:
        self.appended: list[tuple[str, object]] = []

    async def append_call(self, turn_id: str, call) -> None:
        self.appended.append((turn_id, call))


def _noop_run(*_a, **_kw):
    raise AssertionError("run_appointment/run_shop không nên được gọi trong test này")


def make_deps(**overrides) -> OrchestratorDeps:
    defaults = dict(
        db=None,
        user=None,
        ctx=make_ctx(),
        ledger=FakeLedger(),
        turn_id="turn-1",
        question="chào em",
        max_rounds=8,
        run_appointment=_noop_run,
        run_shop=_noop_run,
    )
    defaults.update(overrides)
    return OrchestratorDeps(**defaults)


def _by_name(tools):
    return {t.name: t for t in tools}


def _walk_forbidden(node, keys) -> set:
    found: set = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                found.add(k)
            found |= _walk_forbidden(v, keys)
    elif isinstance(node, list):
        for item in node:
            found |= _walk_forbidden(item, keys)
    return found


def _literal_values(annotation) -> set:
    values: set = set()
    for arg in typing.get_args(annotation):
        if typing.get_origin(arg) is typing.Literal:
            values.update(typing.get_args(arg))
        else:
            values |= _literal_values(arg)
    return values


def _moment(hours: int = 1) -> Moment:
    dt = now_utc() + timedelta(hours=hours)
    return Moment(iso=dt.isoformat(), spoken=f"+{hours}h")


# ---------------------------------------------------------------------------
# Hình dạng bộ tool
# ---------------------------------------------------------------------------


def test_five_tools_with_expected_names_and_return_direct_flags():
    deps = make_deps()
    tools = make_orchestrator_tools(deps)
    by_name = _by_name(tools)

    assert set(by_name) == EXPECTED_NAMES
    for name, t in by_name.items():
        # Không tool nào `return_direct` (ruling Task 3.3): kết thúc lượt do
        # `OrchestratorGuard.abefore_model` nhảy END, để một `finish` bị từ
        # chối không thoát vòng theo tên tool.
        assert not t.return_direct, name


def test_orchestrator_tool_schemas_are_strict_safe():
    deps = make_deps()
    tools = make_orchestrator_tools(deps)

    for t in tools:
        schema = convert_to_openai_tool(t, strict=True)
        forbidden = _walk_forbidden(schema, {"default", "minimum", "maximum"})
        assert forbidden == set(), (t.name, forbidden, schema)
        assert schema["function"]["parameters"].get("additionalProperties") is False, t.name


def test_appointment_id_enum_follows_turn_context():
    ctx = make_ctx(
        upcoming=[
            AppointmentRef(id="a1", start_at=now_utc(), spoken="mai 9h"),
            AppointmentRef(id="a2", start_at=now_utc(), spoken="mai 10h"),
        ]
    )
    deps = make_deps(ctx=ctx)
    tools = make_orchestrator_tools(deps)
    appointment_task = _by_name(tools)["appointment_task"]

    args_cls = appointment_task.args_schema.model_fields["args"].annotation
    appointment_id_annotation = args_cls.model_fields["appointment_id"].annotation

    assert _literal_values(appointment_id_annotation) == {"a1", "a2"}


def test_queue_id_enum_follows_turn_context():
    now = now_utc()
    ctx = make_ctx(
        queued_requests=[
            QueuedRequest(queue_id="q1", start_at=now, spoken="mai 9h", queued_at=now),
            QueuedRequest(queue_id="q2", start_at=now, spoken="mai 10h", queued_at=now),
        ]
    )
    deps = make_deps(ctx=ctx)
    tools = make_orchestrator_tools(deps)
    appointment_task = _by_name(tools)["appointment_task"]

    args_cls = appointment_task.args_schema.model_fields["args"].annotation
    time_hint_cls = args_cls.model_fields["time"].annotation
    # `time` is `TimeHint | None` — unwrap to the actual TimeHint model.
    time_hint_cls = next(
        a for a in typing.get_args(time_hint_cls) if a is not type(None)
    )
    queue_id_annotation = time_hint_cls.model_fields["queue_id"].annotation

    assert _literal_values(queue_id_annotation) == {"q1", "q2"}


def test_tool_docstrings_are_english():
    deps = make_deps()
    tools = make_orchestrator_tools(deps)
    for t in tools:
        assert t.description, t.name
        assert not _VI_DIACRITICS.search(t.description), (t.name, t.description)


# ---------------------------------------------------------------------------
# appointment_task: spec, specialist, ledger
# ---------------------------------------------------------------------------


async def test_appointment_task_builds_spec_and_calls_the_specialist():
    received: dict = {}

    async def fake_run_appointment(spec: TaskSpec, ctx, question: str) -> TaskResult:
        received["spec"] = spec
        received["ctx"] = ctx
        received["question"] = question
        return TaskResult(task_id=spec.task_id, agent=spec.agent, action=spec.action, status="ok")

    deps = make_deps(run_appointment=fake_run_appointment)
    tools = make_orchestrator_tools(deps)
    appointment_task = _by_name(tools)["appointment_task"]
    args_cls = appointment_task.args_schema.model_fields["args"].annotation
    args = args_cls(time=None, service=None, appointment_id=None, for_whom=None, also=None)

    await appointment_task.ainvoke({"action": "resolve_time", "args": args.model_dump()})

    spec = received["spec"]
    assert isinstance(spec, TaskSpec)
    assert spec.task_id == "t0"
    assert spec.agent == "appointment"
    assert spec.action == "resolve_time"
    assert isinstance(spec.args, AppointmentArgs)
    assert received["question"] == deps.question
    assert received["ctx"] is deps.ctx


async def test_appointment_task_itself_does_not_write_call_records():
    """`turn_ledger.calls` has a single writer: `OrchestratorGuard`
    (Task 3.3), which `append_call`s with the real round/index right after
    `awrap_tool_call`'s `handler(...)` returns, before the `ToolMessage`
    reaches the model (§5.3/C1). If `appointment_task` also wrote here, every
    proposal would be recorded twice and `previous_turns` would read it
    twice."""
    moment = _moment()

    async def fake_run_appointment(spec: TaskSpec, ctx, question: str) -> TaskResult:
        data = ProposalCreated(proposal_id="p1", kind="book", start_at=moment, note=None,
                               replaces=None, queued=[])
        return TaskResult(task_id=spec.task_id, agent=spec.agent, action=spec.action,
                          status="ok", data=data)

    ledger = FakeLedger()
    deps = make_deps(run_appointment=fake_run_appointment, ledger=ledger)
    tools = make_orchestrator_tools(deps)
    appointment_task = _by_name(tools)["appointment_task"]
    args_cls = appointment_task.args_schema.model_fields["args"].annotation
    args = args_cls(time=None, service=None, appointment_id=None, for_whom=None, also=None)

    out = await appointment_task.ainvoke({"action": "propose_book", "args": args.model_dump()})

    assert ledger.appended == []

    payload = json.loads(out)
    assert payload["data"]["proposal_id"] == "p1"
