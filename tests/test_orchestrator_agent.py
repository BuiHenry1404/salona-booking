"""Task 3.4: `orchestrator/agent.py` — `build_orchestrator`/`run_orchestrate`,
`orchestrator_message`, `OrchestrateOutcome`, `StrictToolsMiddleware`.

Mọi test dùng `ScriptedChatModel` (`tests/fakes.py`) qua
`monkeypatch.setattr("app.agents.booking.orchestrator.agent.build_chat_model", ...)`
— không gọi Azure. `FakeSpecialists` thay `deps.run_appointment`/`run_shop`.
Ledger là `TurnLedger` THẬT trên `test_db` (brief Task 3.4) vì `run_orchestrate`
đọc lại ledger khi timeout/lỗi model cắt ngang trước khi có state cuối.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import structlog
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking.context import PendingProposal
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.orchestrator.agent import (
    OrchestrateOutcome,
    orchestrator_message,
    run_orchestrate,
)
from app.agents.booking.orchestrator.tools import OrchestratorDeps
from app.agents.booking.prompts.orchestrator import ORCHESTRATOR_PROMPT
from app.agents.booking.schemas import (
    FreeSlots,
    Moment,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    TaskResult,
)
from app.core.clock import TZ, now_utc, to_local
from app.core.config import settings
from app.core.vi_format import format_vi_datetime
from app.services.auth import AuthService
from app.services.proposal import ProposalService
from tests.factories import make_ctx
from tests.fakes import FakeSpecialists, Hang, scripted, tool_call, tool_calls

BUILD_CHAT_MODEL = "app.agents.booking.orchestrator.agent.build_chat_model"
CUSTOMER_WORDS = "chi muon lam mong luc ba gio chieu mai nha em"


# ---------------------------------------------------------------------------
# Dựng deps/ngữ cảnh
# ---------------------------------------------------------------------------


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    """Mốc giờ CỐ ĐỊNH (mai, `hour`:`minute`) — không lệ thuộc giờ chạy test."""
    tomorrow = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=TZ)


async def a_user(db, name="Co Lan"):
    phone = f"09{uuid.uuid4().int % 100000000:08d}"
    return await AuthService(db).create_user(phone, "matkhau123", name)


def _unused(*_a, **_kw):
    raise AssertionError("specialist khong nen duoc goi trong test nay")


async def make_deps(test_db, user, *, ctx=None, run_appointment=None, run_shop=None, max_rounds=None) -> OrchestratorDeps:
    turn_id = f"turn-{uuid.uuid4().hex}"
    ledger = TurnLedger(test_db)
    await ledger.start(
        turn_id=turn_id, user_id=str(user.id), route="orchestrate", rule="R0", asked_at=now_utc()
    )
    return OrchestratorDeps(
        db=test_db,
        user=user,
        ctx=ctx or make_ctx(),
        ledger=ledger,
        turn_id=turn_id,
        question=CUSTOMER_WORDS,
        max_rounds=max_rounds or settings.orch_max_tool_rounds,
        run_appointment=run_appointment or _unused,
        run_shop=run_shop or _unused,
    )


def patch_model(monkeypatch, model) -> None:
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)


# ---------------------------------------------------------------------------
# Dựng dict args tool
# ---------------------------------------------------------------------------


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


def _decide(label="approve", aside=None, reason="khach noi") -> dict:
    return {"label": label, "aside": aside, "reason": reason}


def _finish(stated=None) -> dict:
    return {"kind_hint": "answer", "service": None, "stated": stated or []}


def _shop_hours_result() -> TaskResult:
    open_, close = _tomorrow_at(8), _tomorrow_at(19)
    data = ShopHoursOut(
        open=Moment(iso=open_.isoformat(), spoken="8 gio"),
        close=Moment(iso=close.isoformat(), spoken="19 gio"),
        closed_days=[],
    )
    return TaskResult(task_id="t0", agent="shop_info", action="get_hours", status="ok", data=data)


def _propose_result(hour: int = 9) -> TaskResult:
    data = ProposalCreated(
        proposal_id=f"p-{hour}", kind="book",
        start_at=Moment(iso=_tomorrow_at(hour).isoformat(), spoken=f"{hour} gio sang mai"),
        note=None, replaces=None, queued=[],
    )
    return TaskResult(task_id="t0", agent="appointment", action="propose_book", status="ok", data=data)


def _find_slots_result(day_offset: int) -> TaskResult:
    day = (to_local(now_utc()) + timedelta(days=day_offset)).date()
    data = FreeSlots(day=day, slots=[], more=False, fetched_at=now_utc())
    return TaskResult(task_id="t0", agent="appointment", action="find_slots", status="ok", data=data)


# ---------------------------------------------------------------------------
# finish/ask_customer — kết thúc lượt
# ---------------------------------------------------------------------------


async def test_finish_alone_ends_the_loop_with_ended_by_finish(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([tool_call("finish", _finish(["lam mong"]))], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert isinstance(out, OrchestrateOutcome)
    assert out.ended_by == "finish"
    assert out.finish is not None and out.finish.kind_hint == "answer"
    assert out.ask is None
    assert len(out.calls) == 1
    assert out.calls[0].tool == "finish" and out.calls[0].allowed
    # `rounds_executed` đếm vòng CÓ CALL CHẠY xong trước khi `abefore_model`
    # gọi lại model lần nữa — vòng `finish` tự nó nhảy END ngay, không bao
    # giờ tới nhánh đếm vòng (xem `test_parallel_same_round_updates_do_not_raise`
    # của `test_orchestrator_guard.py`: vòng `finish` cũng không được đếm).
    assert out.rounds_executed == 0


async def test_ask_customer_ends_and_records_ask_args(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted(
        [tool_call("ask_customer", {"field": "time", "hint": "chua ro gio"})], tags=("orchestrate",)
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "ask_customer"
    assert out.ask is not None and out.ask.field == "time" and out.ask.hint == "chua ro gio"
    assert out.finish is None


async def test_finish_next_to_shop_task_is_rejected_then_finish_alone_ends(test_db, clean_db, monkeypatch):
    """§13: vòng 1 `tool_calls(finish, shop_info_task)` → `shop_info_task`
    chạy, `finish` bị `serialize_required`; vòng 2 `finish` một mình → END."""
    user = await a_user(test_db)
    fake = FakeSpecialists({("shop", "get_hours"): _shop_hours_result()})
    deps = await make_deps(test_db, user, run_shop=fake.shop)
    model = scripted(
        [
            tool_calls(("finish", _finish()), ("shop_info_task", _shop("get_hours"))),
            tool_call("finish", _finish()),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "finish"
    rejected = [c for c in out.calls if not c.allowed]
    assert len(rejected) == 1
    assert rejected[0].tool == "finish" and rejected[0].reject_code == "serialize_required"
    assert len(fake.received) == 1 and fake.received[0].action == "get_hours"


# ---------------------------------------------------------------------------
# Một đề xuất/lượt
# ---------------------------------------------------------------------------


async def test_two_proposes_in_one_message_leave_one_proposal(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    fake = FakeSpecialists({("appointment", "propose_book"): _propose_result()})
    deps = await make_deps(test_db, user, run_appointment=fake.appointment)
    model = scripted(
        [
            tool_calls(
                ("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9))),
                ("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=10))),
            ),
            tool_call("finish", _finish()),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "finish"
    assert out.proposals_created == 1
    assert len(fake.received) == 1
    # (g): call index 1 (anh em có index NHỎ HƠN cũng là `propose_*`) bị
    # chặn TRƯỚC khi tới luật (e) — `serialize_required`, không phải
    # `one_proposal_per_turn`.
    rejected = [c for c in out.calls if not c.allowed]
    assert len(rejected) == 1
    assert rejected[0].index == 1
    assert rejected[0].reject_code == "serialize_required"


# ---------------------------------------------------------------------------
# decide_pending → clarify ép dừng
# ---------------------------------------------------------------------------


async def test_unclear_forces_end_and_no_call_runs_afterwards(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    pending = PendingProposal(
        id=str(proposal.id), kind="book", start_at=proposal.start_at,
        spoken=format_vi_datetime(start), note=None, expires_at=proposal.expires_at,
        unclear_count=0, target=None,
    )
    deps = await make_deps(test_db, user, ctx=make_ctx(pending=pending))
    model = scripted(
        [
            tool_call("decide_pending", _decide(label="unclear", reason="khach khong tra loi ro")),
            tool_call("finish", _finish()),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "forced"
    assert out.pending_outcome is not None and out.pending_outcome.outcome == "clarify"
    # `finish` sau đó không bao giờ chạy — ép dừng xảy ra ở `abefore_model`
    # TRƯỚC khi model được gọi lại.
    assert len(model.replies) == 1


# ---------------------------------------------------------------------------
# Trần vòng
# ---------------------------------------------------------------------------


async def test_limit_ends_after_eight_executed_rounds_not_counting_rejected_ones(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    fake = FakeSpecialists({("appointment", "find_slots"): [_find_slots_result(i) for i in range(1, 9)]})
    deps = await make_deps(test_db, user, run_appointment=fake.appointment)

    run_replies = [tool_call("appointment_task", _appt("find_slots", time=_time(day_offset=i))) for i in range(1, 9)]
    rej_replies = [tool_call("book_now", {"n": i}) for i in range(3)]
    replies = [
        rej_replies[0], run_replies[0], rej_replies[1], run_replies[1], rej_replies[2],
        *run_replies[2:], tool_call("finish", _finish()),
    ]
    model = scripted(replies, tags=("orchestrate",))
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "limit"
    assert out.rounds_executed == 8
    assert len(model.replies) == 1  # câu đáp cuối (finish) không bao giờ được dùng


# ---------------------------------------------------------------------------
# Lỗi model — ruling review M3.4 vòng 1: "error" CHỈ khi ledger KHÔNG có call
# nào được phép (chưa ghi gì); đã có call được phép (vd. `decide_pending`
# approve đã ghi lịch) thì lỗi model ở vòng SAU phải là "limit", không phải
# "error" — nếu không, `apply_decision`'s pending_outcome="kept" mặc định của
# nhánh error (chưa áp dụng ở agent.py, nhưng ngữ nghĩa "error" dành cho A)
# sẽ mâu thuẫn với lịch ĐÃ ghi mà khách không được báo (anti-goal §10).
# ---------------------------------------------------------------------------


async def test_model_error_on_first_round_is_ended_by_error(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([RuntimeError("model hong")], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "error"
    assert out.calls == []
    assert out.finish is None and out.ask is None


async def test_model_error_after_an_allowed_call_is_ended_by_limit_not_error(test_db, clean_db, monkeypatch):
    """Vòng 1 `decide_pending(approve)` ghi lịch thật (được phép, vào ledger);
    vòng 2 model ném lỗi → `ended_by` phải là "limit" (đã có tiến triển ghi
    được), KHÔNG phải "error" — và `pending_outcome` vẫn đọc được "approved"
    từ ledger, không bị bịa lại thành "kept"."""
    user = await a_user(test_db)
    start = _tomorrow_at(15)
    proposal = await ProposalService(test_db).create(user, "book", start, None)
    pending = PendingProposal(
        id=str(proposal.id), kind="book", start_at=proposal.start_at,
        spoken=format_vi_datetime(start), note=None, expires_at=proposal.expires_at,
        unclear_count=0, target=None,
    )
    deps = await make_deps(test_db, user, ctx=make_ctx(pending=pending))
    model = scripted(
        [
            tool_call("decide_pending", _decide(label="approve", reason="khach dong y")),
            RuntimeError("model hong o vong 2"),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "limit"
    assert out.pending_outcome is not None and out.pending_outcome.outcome == "approved"
    allowed = [c for c in out.calls if c.allowed]
    assert len(allowed) == 1 and allowed[0].tool == "decide_pending"


# ---------------------------------------------------------------------------
# strict=True
# ---------------------------------------------------------------------------


async def test_strict_is_passed_to_bind_tools(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([tool_call("finish", _finish())], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    await run_orchestrate(deps, recent=[])

    assert any(kw.get("strict") is True for kw in model.bind_kwargs)


# ---------------------------------------------------------------------------
# orchestrator_message
# ---------------------------------------------------------------------------


def test_orchestrator_message_has_at_most_two_recent_pairs_and_untrusted_question():
    ctx = make_ctx()
    recent = [
        HumanMessage("cau hoi 1"), AIMessage("tra loi 1"),
        HumanMessage("cau hoi 2"), AIMessage("tra loi 2"),
        HumanMessage("cau hoi 3"), AIMessage("tra loi 3"),
    ]

    msg = orchestrator_message(ctx, recent, "cau hoi hien tai")

    assert isinstance(msg, HumanMessage)
    content = msg.content
    # Chỉ 2 cặp GẦN NHẤT lọt vào — cặp xa nhất bị cắt.
    assert "cau hoi 1" not in content and "tra loi 1" not in content
    assert "cau hoi 2" in content and "tra loi 2" in content
    assert "cau hoi 3" in content and "tra loi 3" in content
    # Câu khách của các lượt TRƯỚC cũng phải gắn nhãn untrusted từng dòng
    # (review M3.4 vòng 1, finding 3) — không phải bare "Customer: ...".
    assert "Customer (untrusted): cau hoi 2" in content
    assert "Customer (untrusted): cau hoi 3" in content
    assert "Assistant: tra loi 3" in content
    # Câu khách lượt NÀY luôn đứng SAU nhãn UNTRUSTED riêng (nhãn khác với
    # nhãn từng dòng ở trên, nhưng cùng ý nghĩa: chữ khách không bao giờ
    # đứng trước nhãn của nó).
    assert "Customer said (untrusted):\ncau hoi hien tai" in content
    assert content.index("Customer said (untrusted):") > content.index("tra loi 3")


# ---------------------------------------------------------------------------
# Timeout — cùng ruling (e): không call nào được phép → "error"; đã có call
# được phép (đề xuất đã tạo) → "limit", đúng như lỗi model.
# ---------------------------------------------------------------------------


async def test_timeout_before_any_allowed_call_is_ended_by_error(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([Hang(seconds=5)], tags=("orchestrate",))
    patch_model(monkeypatch, model)
    monkeypatch.setattr(settings, "orch_orchestrate_timeout_seconds", 0.1)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "error"
    assert out.calls == []


async def test_timeout_after_an_allowed_call_is_ended_by_limit(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    fake = FakeSpecialists({("appointment", "propose_book"): _propose_result()})
    deps = await make_deps(test_db, user, run_appointment=fake.appointment)
    model = scripted(
        [
            tool_call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9))),
            Hang(seconds=5),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)
    monkeypatch.setattr(settings, "orch_orchestrate_timeout_seconds", 0.1)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "limit"
    assert out.proposals_created == 1
    allowed = [c for c in out.calls if c.allowed]
    assert len(allowed) == 1 and allowed[0].tool == "appointment_task"


async def test_orchestrate_failed_log_has_reason_and_ids(test_db, clean_db, monkeypatch):
    """§11: một sự kiện DUY NHẤT `orchestrate_failed`, `reason` phân biệt
    "timeout"/"model_error", `extra` mang đủ `user_id`/`turn_id`/`thread_id`."""
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([RuntimeError("model hong")], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    with structlog.testing.capture_logs() as logs:
        await run_orchestrate(deps, recent=[], thread_id="thread-abc")

    failed = [e for e in logs if e["event"] == "orchestrate_failed"]
    assert len(failed) == 1
    extra = failed[0]["extra"]
    assert extra["reason"] == "model_error"
    assert extra["user_id"] == str(user.id)
    assert extra["turn_id"] == deps.turn_id
    assert extra["thread_id"] == "thread-abc"


async def test_run_orchestrate_passes_thread_id_into_guard_logs(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([tool_call("finish", _finish())], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    with structlog.testing.capture_logs() as logs:
        await run_orchestrate(deps, recent=[], thread_id="thread-xyz")

    orch = [e for e in logs if e["event"] == "orch_call"]
    assert orch and orch[0]["extra"]["thread_id"] == "thread-xyz"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_orchestrator_prompt_is_english_and_names_the_two_ending_tools():
    assert ORCHESTRATOR_PROMPT.isascii()
    assert "finish" in ORCHESTRATOR_PROMPT
    assert "ask_customer" in ORCHESTRATOR_PROMPT


def test_orchestrator_prompt_mentions_social_and_unsupported_kind_hints():
    """Task 4.4b: không còn bộ phân loại riêng (`classify_only`) — prompt
    phải tự nói Orchestrator khi nào gọi `finish(kind_hint="social")` và khi
    nào `finish(kind_hint="unsupported")`. Chỉ kiểm có mặt, không ghim câu chữ."""
    assert "kind_hint" in ORCHESTRATOR_PROMPT
    assert "social" in ORCHESTRATOR_PROMPT
    assert "unsupported" in ORCHESTRATOR_PROMPT


async def test_social_chitchat_ends_by_finish_without_any_specialist_call(test_db, clean_db, monkeypatch):
    """"chào em" kiểu thuần xã giao → model gọi thẳng `finish(kind_hint="social")`,
    không specialist nào chạy (Task 4.4b thay cho `classify_only`/R4 cũ)."""
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted(
        [tool_call("finish", {"kind_hint": "social", "service": None, "stated": []})],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "finish"
    assert out.finish is not None and out.finish.kind_hint == "social"
    assert len(out.calls) == 1 and out.calls[0].tool == "finish"


# ---------------------------------------------------------------------------
# Ruling controller (sau Task 3.3): mỗi lượt dựng state MỚI
# ---------------------------------------------------------------------------


async def test_two_consecutive_turns_do_not_carry_ended_by_over(test_db, clean_db, monkeypatch):
    """`checkpointer=False` + state mới mỗi lượt: lượt 2 (ended_by khác lượt 1)
    phải thật sự gọi model, không nhảy END ngay vì `ended_by` còn sót từ lượt 1."""
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)

    model1 = scripted([tool_call("finish", _finish())], tags=("orchestrate",))
    patch_model(monkeypatch, model1)
    out1 = await run_orchestrate(deps, recent=[])
    assert out1.ended_by == "finish"

    model2 = scripted([tool_call("ask_customer", {"field": "time", "hint": None})], tags=("orchestrate",))
    patch_model(monkeypatch, model2)
    out2 = await run_orchestrate(deps, recent=[])

    assert out2.ended_by == "ask_customer"
    assert len(model2.calls) == 1  # model2 THẬT SỰ được gọi, không bị bỏ qua


async def test_orchestrator_model_runs_with_low_reasoning_effort(test_db, clean_db, monkeypatch):
    """Task 4.4c (quyết định chủ dự án 06/11): Orchestrator bật `reasoning_effort="low"`.
    `build_chat_model` tự bỏ `temperature` khi có `reasoning_effort` (llm.py)."""
    from app.agents.booking.orchestrator.agent import build_orchestrator

    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    captured: dict = {}

    def fake_build(**kw):
        captured.update(kw)
        return scripted([tool_call("finish", _finish())], tags=("orchestrate",))

    monkeypatch.setattr(BUILD_CHAT_MODEL, fake_build)
    build_orchestrator(deps)

    assert captured["reasoning_effort"] == "low"
    assert captured["tags"] == ["orchestrate"]
    assert captured["streaming"] is False
    assert captured["max_retries"] == 0


# ---------------------------------------------------------------------------
# M6b L2: model dừng bằng chữ thường (không tool call)
# ---------------------------------------------------------------------------


async def test_plain_text_after_an_allowed_call_ends_as_finish_answer(test_db, clean_db, monkeypatch):
    """Lượt 3/4/9 chạy thật: sau `propose_book` model trả AIMessage không tool
    call → code coi là `finish(kind_hint="answer")`, không phải `limit`."""
    user = await a_user(test_db)
    fake = FakeSpecialists({("appointment", "propose_book"): _propose_result()})
    deps = await make_deps(test_db, user, run_appointment=fake.appointment)
    model = scripted(
        [
            tool_call("appointment_task", _appt("propose_book", time=_time(day_offset=1, hour=9))),
            AIMessage("Da em giu cho roi"),
        ],
        tags=("orchestrate",),
    )
    patch_model(monkeypatch, model)

    with structlog.testing.capture_logs() as logs:
        out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "finish"
    assert out.finish is not None and out.finish.kind_hint == "answer"
    assert out.rounds_executed == 1
    assert out.proposals_created == 1
    text_end = [e for e in logs if e["event"] == "orch_text_end"]
    assert text_end and text_end[0]["extra"]["turn_id"] == deps.turn_id


async def test_plain_text_with_no_allowed_call_is_still_error(test_db, clean_db, monkeypatch):
    user = await a_user(test_db)
    deps = await make_deps(test_db, user)
    model = scripted([AIMessage("Da em chao chi")], tags=("orchestrate",))
    patch_model(monkeypatch, model)

    out = await run_orchestrate(deps, recent=[])

    assert out.ended_by == "error"
    assert out.finish is None
