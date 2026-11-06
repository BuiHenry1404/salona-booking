"""Task 5.3: `app/agents/booking/graph.py` — vỏ LangGraph một lượt khách.

Mongo thật (`test_db`), model giả: fixture `models` cắm `ScriptedChatModel`
vào 4 chỗ dựng model (`orchestrator.agent`, `conversation`, `specialists.base`,
`booking_graph.rewrite`); specialist thay bằng `FakeSpecialists` qua
monkeypatch `graph.appointment.run_task`/`graph.shop_info.run_task`. Không
gọi Azure. Mốc giờ cố định (`_tomorrow_at`) như `tests/test_orchestrator_decide.py`.

Chỉ kiểm phần graph THÊM vào (định tuyến, thứ tự node, đọc/ghi ledger, thread,
trần ngân sách, ghi đè `limit_reached`, log) — hành vi riêng của từng module
đã có test ở tầng của nó.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.booking import graph as graph_module
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.llm_budget import LLM_KEY, BudgetMiddleware, LlmBudget
from app.agents.booking.schemas import FreeSlots, Moment, PendingOutcome, ResolvedTime, TaskResult
from app.agents.booking_graph.button import ButtonReply
from app.agents.booking_graph.context import address_phrase
from app.agents.booking_graph.history import stamp
from app.agents.booking_graph.middleware.tools import FALLBACK_ANSWER
from app.core.clock import TZ, now_ms, now_utc, to_local
from app.core.config import settings
from app.core.vi_format import format_vi_datetime
from app.models.conversation import GoalProgress
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation_state import ConversationStateService
from app.services.proposal import ProposalService
from tests.fakes import FakeSpecialists, scripted, tool_call

ORCH_MODEL = "app.agents.booking.orchestrator.agent.build_chat_model"
CONV_MODEL = "app.agents.booking.conversation.build_chat_model"
SPECIALIST_MODEL = "app.agents.booking.specialists.base.build_chat_model"
REWRITE_MODEL = "app.agents.booking_graph.rewrite.build_chat_model"

PLANNED_NODES = {"resolve", "button_path", "orchestrate", "brief", "respond", "record"}


# ---------------------------------------------------------------------------
# Dựng dữ liệu
# ---------------------------------------------------------------------------


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    """Mốc giờ Việt Nam CỐ ĐỊNH (mai) — không lệ thuộc giờ chạy test thật."""
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


@pytest.fixture
async def user(test_db):
    phone = f"09{uuid.uuid4().int % 100000000:08d}"
    return await AuthService(test_db).create_user(phone, "matkhau123", "Nguyễn Thị Lan")


@pytest.fixture
def models(monkeypatch):
    """Model giả cho 4 chỗ dựng model. Mỗi vai một `ScriptedChatModel` dùng
    chung cho mọi lần dựng trong lượt — test gán `replies` trước khi chạy.
    Specialist/rewrite để rỗng: gọi tới là `IndexError` (lộ ra ngay)."""
    bag = SimpleNamespace(
        orchestrator=scripted([], tags=("orchestrate",)),
        conversation=scripted([], tags=("respond",)),
        specialist=scripted([], tags=("appointment",)),
        rewrite=scripted([], tags=("rewrite",)),
        specialists=FakeSpecialists({}),
        specialist_middleware=[],
    )
    monkeypatch.setattr(ORCH_MODEL, lambda **kw: bag.orchestrator)
    monkeypatch.setattr(CONV_MODEL, lambda **kw: bag.conversation)
    monkeypatch.setattr(SPECIALIST_MODEL, lambda **kw: bag.specialist)
    monkeypatch.setattr(REWRITE_MODEL, lambda **kw: bag.rewrite)

    async def fake_appointment(db, user, spec, ctx, question, *, extra_middleware=()):
        bag.specialist_middleware.append(list(extra_middleware))
        return await bag.specialists.appointment(spec, ctx, question)

    async def fake_shop(db, user, spec, ctx, question, *, extra_middleware=()):
        bag.specialist_middleware.append(list(extra_middleware))
        return await bag.specialists.shop(spec, ctx, question)

    monkeypatch.setattr(graph_module.appointment, "run_task", fake_appointment)
    monkeypatch.setattr(graph_module.shop_info, "run_task", fake_shop)
    return bag


def _finish(kind_hint="answer") -> AIMessage:
    return tool_call("finish", {"kind_hint": kind_hint, "service": None, "stated": []})


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


def _free_slots(day_offset: int = 1) -> TaskResult:
    day = (to_local(now_utc()) + timedelta(days=day_offset)).date()
    first = datetime(day.year, day.month, day.day, 9, tzinfo=TZ)
    second = datetime(day.year, day.month, day.day, 10, tzinfo=TZ)
    data = FreeSlots(
        day=day,
        slots=[
            Moment(iso=first.isoformat(), spoken=format_vi_datetime(first)),
            Moment(iso=second.isoformat(), spoken=format_vi_datetime(second)),
        ],
        more=False,
        fetched_at=now_utc(),
    )
    return TaskResult(task_id="t0", agent="appointment", action="find_slots", status="ok", data=data)


def _unresolved_time() -> TaskResult:
    data = ResolvedTime(start_at=None, partial_date=None, missing=["giờ cụ thể"], day_from=None)
    return TaskResult(task_id="t0", agent="appointment", action="resolve_time", status="needs_info", data=data)


async def run_turn(test_db, user, text: str, *, button: ButtonReply | None = None,
                   asked_at: datetime | None = None, previous_replies=None, thread_id=None):
    """Chạy MỘT lượt như `events.py` (Task 5.4) sẽ chạy: input là câu khách
    đã stamp + khoá của lượt, luôn từ START. Trả `(state cuối, thread_id)`."""
    asked_at = asked_at or now_ms()
    thread_id = thread_id or f"{user.id}:test"
    graph = graph_module.build_graph(test_db, user)
    fields = {
        "messages": [stamp(HumanMessage(content=text), asked_at)],
        "turn_id": uuid.uuid4().hex,
        "asked_at": asked_at,
        "history_after": None,
        "customer_text": text,
        "button": asdict(button) if button is not None else None,
        "previous_replies": previous_replies or [],
    }
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.ainvoke(fields, config)
    return state, graph, config


async def _llm_hits(test_db, user) -> int:
    return await test_db["rate_limits"].count_documents({"key": LLM_KEY.format(user_id=str(user.id))})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_graph_has_exactly_the_planned_nodes(test_db, user):
    graph = graph_module.build_graph(test_db, user)
    nodes = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == PLANNED_NODES


async def test_r4_social_goes_orchestrate_then_finish_social_then_respond(test_db, user, models):
    models.orchestrator.replies = [_finish("social")]
    models.conversation.replies = [AIMessage(content="Dạ em chào chị Lan ạ.")]

    state, _graph, _config = await run_turn(test_db, user, "chào em")

    assert state["resolve"]["route"] == "orchestrate"
    assert state["resolve"]["rule"] == "R5"
    assert state["brief"]["kind"] == "social"
    assert state["answer"] == "Dạ em chào chị Lan ạ."
    assert models.specialists.received == []
    assert len(models.conversation.calls) == 1


async def test_r5_runs_orchestrator_then_brief_then_respond_once(test_db, user, models):
    slots = _free_slots()
    models.specialists = FakeSpecialists({("appointment", "find_slots"): slots})
    models.orchestrator.replies = [
        tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=1))),
        _finish(),
    ]
    spoken = [m.spoken for m in slots.data.slots]
    models.conversation.replies = [AIMessage(content=f"Dạ mai còn {spoken[0]} và {spoken[1]} chị nhé.")]

    state, _graph, _config = await run_turn(test_db, user, "mai còn giờ nào trống không em")

    assert state["brief"]["kind"] == "answer"
    assert state["answer_source"] == "llm"
    assert len(models.conversation.calls) == 1
    assert [s.action for s in models.specialists.received] == ["find_slots"]
    # Ngân sách: mỗi lời gọi model THẬT một dấu — 2 của Orchestrator + 1 của Conversation.
    assert await _llm_hits(test_db, user) == len(models.orchestrator.calls) + 1
    # Specialist nhận đúng `BudgetMiddleware` của lượt (đường truyền budget).
    assert len(models.specialist_middleware) == 1
    assert any(isinstance(m, BudgetMiddleware) for m in models.specialist_middleware[0])


async def test_r1_button_yes_writes_and_speaks_the_confirmation(test_db, user, models):
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    spoken = format_vi_datetime(start)
    models.conversation.replies = [AIMessage(content=f"Dạ em đặt xong {spoken} cho chị Lan rồi nhé.")]
    button = ButtonReply(decision="yes", proposal_id=str(proposal.id), label="Đồng ý")

    state, _graph, _config = await run_turn(
        test_db, user, "Đồng ý", button=button,
        asked_at=proposal.created_at + timedelta(seconds=1),
    )

    assert state["resolve"]["route"] == "button"
    assert state["brief"]["kind"] == "confirmed"
    assert spoken in state["answer"]
    assert models.orchestrator.calls == []
    assert len(models.conversation.calls) == 1
    appointments = await AppointmentService(test_db).upcoming_for(user)
    assert len(appointments) == 1
    # Fix round 1: nút của lượt này không được rò sang lượt gõ chữ sau.
    assert state["button"] is None


@pytest.mark.parametrize("decision", ["yes", "no"])
async def test_button_decision_closes_the_goal(test_db, user, models, decision):
    await ConversationStateService(test_db).record_goal_progress(
        str(user.id), GoalProgress(goal_id="g1", turns_without_progress=settings.orch_goal_turns_limit - 1),
    )
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    spoken = format_vi_datetime(start)
    reply = f"Dạ em đặt xong {spoken} cho chị Lan rồi nhé." if decision == "yes" else "Dạ vâng chị Lan."
    models.conversation.replies = [AIMessage(content=reply)]
    button = ButtonReply(decision=decision, proposal_id=str(proposal.id), label="Đồng ý")

    state, _graph, _config = await run_turn(
        test_db, user, "Đồng ý", button=button,
        asked_at=proposal.created_at + timedelta(seconds=1),
    )

    assert state["goal_limit_hit"] is False
    saved = await ConversationStateService(test_db).conversations.get_state(str(user.id))
    assert saved.goal == GoalProgress()


async def test_stale_button_speaks_the_template_without_a_model_call(test_db, user, models):
    button = ButtonReply(decision="yes", proposal_id="khong-con-ton-tai", label="Đồng ý")

    state, _graph, _config = await run_turn(test_db, user, "Đồng ý", button=button)

    assert state["brief"]["kind"] == "stale_button"
    assert state["answer"] == state["brief"]["fallback"]
    assert state["answer_source"] == "code"
    assert models.conversation.calls == []
    assert models.orchestrator.calls == []
    assert await _llm_hits(test_db, user) == 0


async def test_record_appends_only_question_and_answer_to_the_thread(test_db, user, models):
    models.specialists = FakeSpecialists({("appointment", "find_slots"): _free_slots()})
    models.orchestrator.replies = [
        tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=1))),
        _finish(),
    ]
    slots = _free_slots().data.slots
    models.conversation.replies = [AIMessage(content=f"Dạ còn {slots[0].spoken} và {slots[1].spoken} ạ.")]

    _state, graph, config = await run_turn(test_db, user, "mai còn giờ nào")

    messages = (await graph.aget_state(config)).values["messages"]
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert not any(isinstance(m, ToolMessage) for m in messages)
    assert messages[0].content == "mai còn giờ nào"
    assert messages[1].content == _state["answer"]
    assert not messages[1].tool_calls


@pytest.mark.parametrize(
    "orchestrator_reply, ended_by, reply_kind, ask_field",
    [
        (_finish(), "finish", "answer", None),
        (tool_call("ask_customer", {"field": "day", "hint": None}), "ask_customer", "clarify", "day"),
    ],
)
async def test_ledger_is_finished_with_ended_by_and_reply_kind(
    test_db, user, models, orchestrator_reply, ended_by, reply_kind, ask_field
):
    models.orchestrator.replies = [orchestrator_reply]
    models.conversation.replies = [AIMessage(content="Dạ chị muốn hẹn hôm nào ạ?")]

    state, _graph, _config = await run_turn(test_db, user, "em ơi cho chị hẹn")

    record = await TurnLedger(test_db).record_of(state["turn_id"])
    assert record is not None
    assert record["route"] == "orchestrate"
    assert record["rule"] == "R5"
    assert record["ended_by"] == ended_by
    assert record["reply_kind"] == reply_kind
    assert record["ask_field"] == ask_field


@pytest.mark.parametrize("over_budget, expected_rounds", [(True, 2), (False, 3)])
async def test_over_budget_caps_rounds_at_two(test_db, user, models, monkeypatch, over_budget, expected_rounds):
    monkeypatch.setattr(settings, "orch_llm_calls_per_user_per_hour", 1 if over_budget else 1000)
    await LlmBudget(test_db, str(user.id)).hit()
    models.specialists = FakeSpecialists({
        ("appointment", "find_slots"): [_free_slots(1), _free_slots(2), _free_slots(3)],
    })
    models.orchestrator.replies = [
        tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=1))),
        tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=2))),
        tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=3))),
        _finish(),
    ]
    models.conversation.replies = [AIMessage(content="Dạ em xem rồi ạ.")]

    state, _graph, _config = await run_turn(test_db, user, "tuần này còn giờ nào")

    assert state["outcome"]["rounds_executed"] == expected_rounds
    assert len(models.specialists.received) == expected_rounds
    assert state["outcome"]["ended_by"] == ("limit" if over_budget else "finish")


async def test_goal_limit_turns_kind_into_limit_reached_with_the_phone(test_db, user, models):
    await ConversationStateService(test_db).record_goal_progress(
        str(user.id), GoalProgress(goal_id="g1", turns_without_progress=settings.orch_goal_turns_limit - 1),
    )
    models.specialists = FakeSpecialists({("appointment", "resolve_time"): _unresolved_time()})
    models.orchestrator.replies = [
        tool_call("appointment_task", _appt("resolve_time", time=_time(part_of_day="chieu"))),
        _finish(),
    ]
    phone_line = graph_module.limit_line(address_phrase(user))
    models.conversation.replies = [AIMessage(content=f"Dạ em xin lỗi. {phone_line}")]

    state, _graph, _config = await run_turn(test_db, user, "chiều chiều gì đó em")

    assert state["goal_limit_hit"] is True
    assert state["brief"]["kind"] == "limit_reached"
    phones = [f for f in state["brief"]["facts"] if f["key"] == "shop_phone"]
    assert phones and phones[0]["text"] == settings.shop_phone
    # §7.2: lời mời gọi tiệm là câu bắt buộc, và cũng là câu chốt của guard.
    assert state["brief"]["must_say"][-1] == phone_line
    assert state["brief"]["fallback"] == phone_line
    assert phone_line in state["answer"]
    record = await TurnLedger(test_db).record_of(state["turn_id"])
    assert record["reply_kind"] == "limit_reached"
    saved = await ConversationStateService(test_db).conversations.get_state(str(user.id))
    assert saved.goal.goal_id == "g1"
    assert saved.goal.turns_without_progress == 0


async def test_orchestrator_error_on_first_round_speaks_the_hard_fallback_without_the_conversation_model(
    test_db, user, models
):
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    models.orchestrator.replies = [ValueError("model hong"), ValueError("model hong")]

    state, _graph, _config = await run_turn(
        test_db, user, "ừ được em", asked_at=proposal.created_at + timedelta(seconds=1),
    )

    assert state["resolve"]["rule"] == "R2"
    assert state["outcome"]["ended_by"] == "error"
    assert state["answer"] == FALLBACK_ANSWER
    assert state["answer_source"] == "code"
    assert models.conversation.calls == []
    # Pending coi là kept: đề xuất vẫn chờ, không có lịch nào được ghi.
    reloaded = await ProposalService(test_db).get(user, str(proposal.id))
    assert reloaded.status == "pending"
    assert await AppointmentService(test_db).upcoming_for(user) == []


async def test_structured_logs_carry_user_turn_and_thread_ids(test_db, user, models):
    models.orchestrator.replies = [_finish("social")]
    models.conversation.replies = [AIMessage(content="Dạ em chào chị Lan ạ.")]
    thread_id = f"{user.id}:log-test"

    with structlog.testing.capture_logs() as logs:
        state, _graph, _config = await run_turn(test_db, user, "chào em", thread_id=thread_id)

    by_event = {entry["event"]: entry for entry in logs}
    for name in ("resolve_done", "turn_ended"):
        assert name in by_event, name
        extra = by_event[name]["extra"]
        assert extra["user_id"] == str(user.id)
        assert extra["turn_id"] == state["turn_id"]
        assert extra["thread_id"] == thread_id
    # Không log nguyên văn câu khách ngoài turn_ledger (§11).
    assert not any("chào em" in str(entry) for entry in logs)


async def _seed_unannounced_turn(test_db, user, asked_at: datetime) -> tuple[str, str]:
    """Lượt TRƯỚC (cùng ngày) có kết quả khách chưa được báo (C1). Dùng
    `failed`: dòng R0 của `approved` ("đã đặt xong <giờ>") hiện bị guard gỡ
    như một claim sai vì `prior_outcome` không mang `iso` — xem báo cáo
    fix round 1 Task 5.3."""
    ledger = TurnLedger(test_db)
    spoken = format_vi_datetime(_tomorrow_at(15))
    await ledger.start(
        turn_id="prev-turn", user_id=str(user.id), route="orchestrate", rule="R2",
        asked_at=asked_at - timedelta(seconds=1),
    )
    await ledger.set_pending_outcome(
        "prev-turn", PendingOutcome(
            outcome="failed", proposal_id="p-prev", kind="book", spoken=spoken, error="slot_taken",
        ),
    )
    return "prev-turn", spoken


async def test_reported_r0_line_names_the_turn_to_mark_announced(test_db, user, models):
    asked_at = now_ms()
    prev_turn, spoken = await _seed_unannounced_turn(test_db, user, asked_at)
    models.orchestrator.replies = [_finish("social")]
    models.conversation.replies = [AIMessage(content=f"Dạ chị Lan, {spoken} em chưa ghi được ạ.")]

    state, _graph, _config = await run_turn(test_db, user, "alo em", asked_at=asked_at)

    assert state["resolve"]["must_announce"] is True
    assert state["announced_turn_id"] == prev_turn


async def test_r0_line_missing_from_the_answer_leaves_the_turn_unannounced(test_db, user, models):
    asked_at = now_ms()
    await _seed_unannounced_turn(test_db, user, asked_at)
    # Lỗi vòng đầu → câu cứng (không có dòng R0) → lượt trước vẫn chưa báo.
    models.orchestrator.replies = [ValueError("model hong"), ValueError("model hong")]

    state, _graph, _config = await run_turn(test_db, user, "alo em", asked_at=asked_at)

    assert state["answer"] == FALLBACK_ANSWER
    assert state["announced_turn_id"] is None


async def test_failed_set_ask_still_finishes_the_ledger(test_db, user, models, monkeypatch):
    async def broken_set_ask(self, turn_id, field):
        raise RuntimeError("mongo hong")

    monkeypatch.setattr(TurnLedger, "set_ask", broken_set_ask)
    models.orchestrator.replies = [tool_call("ask_customer", {"field": "day", "hint": None})]
    models.conversation.replies = [AIMessage(content="Dạ chị muốn hẹn hôm nào ạ?")]

    state, _graph, _config = await run_turn(test_db, user, "em ơi cho chị hẹn")

    record = await TurnLedger(test_db).record_of(state["turn_id"])
    assert record["ended_by"] == "ask_customer"
    assert record["reply_kind"] == "clarify"
