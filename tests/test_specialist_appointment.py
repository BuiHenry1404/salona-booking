"""Test cho Appointment Agent (Task 2.4, §8.1) — `run_task` đối chiếu
ToolMessage thật với kết quả model trả, không tin chữ model khi nó đáng ra
phải đến từ DB/tool. Dùng model giả (không gọi Azure thật)."""
import uuid
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage

from app.agents.booking.schemas import ProposalCreated, ResolvedTime
from app.agents.booking.specialists.appointment import ALLOWLIST, run_task
from app.agents.booking.prompts.appointment import APPOINTMENT_PROMPT
from app.core.clock import TZ, now_utc
from app.core.vi_format import format_vi_datetime
from app.models.conversation import QueuedRequest
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation import ConversationService
from app.services.conversation_state import ConversationStateService
from tests.factories import make_ctx, make_spec
from tests.fakes import EchoTool as _EchoTool
from tests.fakes import TwoProposeAttempts as _TwoProposeAttempts
from tests.fakes import book_output as _book_output
from tests.fakes import final_output, scripted, tool_call


def _patch_build_chat_model(monkeypatch, model):
    monkeypatch.setattr(
        "app.agents.booking.specialists.base.build_chat_model", lambda **kwargs: model
    )


async def a_user(db, phone=None, name="Co Lan"):
    if phone is None:
        phone = f"09{uuid.uuid4().int % 100000000:08d}"
    await db["users"].delete_many({"phone": phone})
    return await AuthService(db).create_user(phone, "matkhau123", name)


def tomorrow_at(hour: int, minute: int = 0) -> datetime:
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _resolved_time_output(tool_json: dict) -> dict:
    """`parse_time` không cấp `spoken` cho `start_at` (khác `_moment()`) — model
    tự soạn câu nói từ `iso`; test mô phỏng đúng việc đó bằng cùng công thức
    hiển thị tiếng Việt mà prompt sẽ dạy model dùng."""
    start_at = None
    if tool_json.get("start_at"):
        dt = datetime.fromisoformat(tool_json["start_at"])
        start_at = {"iso": tool_json["start_at"], "spoken": format_vi_datetime(dt)}
    return {
        "status": "ok",
        "data": {
            "start_at": start_at,
            "partial_date": tool_json.get("partial_date"),
            "missing": tool_json.get("missing", []),
            "day_from": tool_json.get("day_from"),
        },
        "missing": [], "error_code": None,
    }


async def test_resolve_time_runs_parse_time_and_returns_resolved(test_db, monkeypatch):
    user = await a_user(test_db)
    when = tomorrow_at(15)
    model = _EchoTool(
        tool_name="parse_time",
        tool_args={"day": when.date().isoformat(), "hour": 15, "minute": 0},
        build_output=_resolved_time_output,
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="resolve_time", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "mai 3h chieu nhe")

    assert result.status == "ok"
    assert isinstance(result.data, ResolvedTime)
    assert result.data.start_at is not None
    assert result.data.start_at.iso.startswith(when.date().isoformat())


async def test_propose_book_creates_exactly_one_proposal_and_reports_its_id(test_db, monkeypatch):
    user = await a_user(test_db)
    when = tomorrow_at(15)
    model = _EchoTool(
        tool_name="propose_appointment",
        tool_args={"start_at": when.isoformat(), "note": "lam mong"},
        build_output=_book_output,
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 3h chieu")

    assert result.status == "ok"
    assert isinstance(result.data, ProposalCreated)
    docs = await test_db["proposals"].find(
        {"user_id": str(user.id), "status": "pending"}
    ).to_list(10)
    assert len(docs) == 1
    assert str(docs[0]["_id"]) == result.data.proposal_id


async def test_second_propose_in_the_same_task_is_blocked_and_db_has_one(test_db, monkeypatch):
    user = await a_user(test_db)
    first = tomorrow_at(15)
    second = tomorrow_at(17)
    model = _TwoProposeAttempts(
        first_args={"start_at": first.isoformat(), "note": "lam mong"},
        second_args={"start_at": second.isoformat(), "note": "lam toc"},
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 3h chieu, doi y 5h chieu")

    assert result.status == "ok"
    assert isinstance(result.data, ProposalCreated)
    docs = await test_db["proposals"].find({"user_id": str(user.id)}).to_list(10)
    assert len(docs) == 1
    assert str(docs[0]["_id"]) == result.data.proposal_id


async def test_mismatched_proposal_id_is_schema_invalid(test_db, monkeypatch):
    user = await a_user(test_db)
    when = tomorrow_at(15)
    model = _EchoTool(
        tool_name="propose_appointment",
        tool_args={"start_at": when.isoformat(), "note": "lam mong"},
        build_output=lambda j: {
            "status": "ok",
            "data": {
                "proposal_id": "0" * 24, "kind": "book",
                "start_at": j["held"], "note": j.get("note"),
                "replaces": None, "queued": [],
            },
            "missing": [], "error_code": None,
        },
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 3h chieu")

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"


async def test_propose_move_without_appointment_id_fails_before_the_model(test_db, monkeypatch):
    user = await a_user(test_db)
    model = scripted([])
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_move", task_id="t1.0", appointment_id=None)
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "doi lich qua 5h chieu")

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"
    assert model.calls == []


async def test_tool_outside_allowlist_does_not_exist_in_the_task(test_db, monkeypatch):
    user = await a_user(test_db)
    model = scripted([
        tool_call("find_free_slots", {"day": tomorrow_at(0).date().isoformat()}),
        AIMessage(content="khong lam duoc"),
    ], tags=["appointment"])
    _patch_build_chat_model(monkeypatch, model)
    assert "find_free_slots" not in ALLOWLIST["resolve_time"]
    spec = make_spec(agent="appointment", action="resolve_time", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "mai 3h chieu nhe")

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"


async def test_slot_unavailable_fills_asked_and_nearest_from_the_tool_json(test_db, monkeypatch):
    owner = await a_user(test_db, phone="0938111222", name="Co Hoa")
    user = await a_user(test_db)
    taken = tomorrow_at(16)
    await AppointmentService(test_db).create(owner, taken, note=None)

    model = scripted([
        tool_call("propose_appointment", {"start_at": taken.isoformat(), "note": None}),
        final_output(status="failed", error_code="slot_unavailable"),
    ], tags=["appointment"])
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 4h chieu")

    assert result.status == "failed"
    assert result.error_code == "slot_unavailable"
    assert result.asked is not None
    assert result.asked.iso.startswith(taken.date().isoformat())
    assert result.nearest, "phai co goi y gio khac"


async def test_queued_requests_are_persisted_via_set_queued(test_db, monkeypatch):
    user = await a_user(test_db)
    when = tomorrow_at(15)
    extra = tomorrow_at(17)
    model = _EchoTool(
        tool_name="propose_appointment",
        tool_args={
            "start_at": when.isoformat(), "note": "lam mong",
            "queued": [{"start_at": extra.isoformat(), "note": "lam toc"}],
        },
        build_output=lambda j: {
            "status": "ok",
            "data": {
                "proposal_id": j["proposal_id"], "kind": "book",
                "start_at": j["held"], "note": j.get("note"),
                "replaces": None,
                "queued": [item["at"] for item in j.get("queued", [])],
            },
            "missing": [], "error_code": None,
        },
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 3h chieu, roi 5h chieu nua")

    assert result.status == "ok"
    assert isinstance(result.data, ProposalCreated)
    assert len(result.data.queued) == 1
    assert result.data.queued[0].iso.startswith(extra.date().isoformat())

    state = await ConversationService(test_db).get_state(str(user.id))
    assert state is not None
    assert len(state.queued) == 1
    assert state.queued[0].queue_id == "q1"
    assert state.queued[0].spoken == result.data.queued[0].spoken


async def test_an_emptied_queue_from_the_tool_clears_previously_queued_requests(test_db, monkeypatch):
    """`propose_appointment(queued=[])` là cách khách RÚT hết hàng chờ cũ (tài
    liệu của tool) — `"queued": []` trong JSON tool phải xoá `state.queued`,
    không được coi falsy như "không có gì để làm" (fix round 1)."""
    user = await a_user(test_db)
    now = now_utc()
    await ConversationStateService(test_db).set_queued(str(user.id), [
        QueuedRequest(queue_id="q1", start_at=tomorrow_at(17), spoken="5 gio chieu mai",
                      note="lam toc", queued_at=now),
    ])

    when = tomorrow_at(15)
    model = _EchoTool(
        tool_name="propose_appointment",
        tool_args={"start_at": when.isoformat(), "note": "lam mong", "queued": []},
        build_output=lambda j: {
            "status": "ok",
            "data": {
                "proposal_id": j["proposal_id"], "kind": "book",
                "start_at": j["held"], "note": j.get("note"),
                "replaces": None, "queued": [],
            },
            "missing": [], "error_code": None,
        },
    )
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="appointment", action="propose_book", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "dat mai 3h chieu, bo gio 5h chieu di")

    assert result.status == "ok"
    assert isinstance(result.data, ProposalCreated)
    assert result.data.queued == []

    state = await ConversationService(test_db).get_state(str(user.id))
    assert state is not None
    assert state.queued == []


def test_appointment_prompt_is_english_and_mentions_anchor_precedence():
    assert all(ord(c) < 128 for c in APPOINTMENT_PROMPT)
    assert "anchor" in APPOINTMENT_PROMPT
