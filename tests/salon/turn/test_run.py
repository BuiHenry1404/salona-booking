"""TDD cho `run_turn` (Task 15, spec §3.2, §3.4, §5, §5.1, §8; docs/architech-agent.md
luật 3, 7, 8).

Mỗi test chạy `run_turn` thật trên Mongo thật: Orchestrator (`build_orchestrator`)
với chồng middleware thật, agent con thật, tool thật — chỉ MODEL là giả
(`tests/fakes.py`, không Azure): model Orchestrator thay ở
`orchestrator/agent.py`, model agent con ở `subagents/dispatch.py`, model nén ở
`orchestrator/middleware`, model gom bộ nhớ ở `memory/long_term.py`.
Checkpointer / store là `InMemorySaver` / `InMemoryStore` của fixture autouse.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta
from typing import Optional

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import checkpoint
from app.agents.salon import TURN_ERROR_MESSAGE, AgentEvent, ButtonReply, run_turn
from app.agents.salon.guard.catalog import short_label
from app.agents.salon.guard.fallback import (
    BUTTON_STALE_REPLY,
    FIELD_QUESTION,
    GENERIC_FALLBACK,
    TAKEN_REPLY,
)
from app.agents.salon.memory import long_term
from app.agents.salon.memory.long_term import MEMORY_NAMESPACE
from app.agents.salon.orchestrator import agent as agent_module
from app.agents.salon.orchestrator import middleware as middleware_pkg
from app.agents.salon.orchestrator.middleware import reply_guard as reply_guard_module
from app.agents.salon.orchestrator.prompt import ORCHESTRATOR_PROMPT
from app.agents.salon.shared import context as context_module
from app.agents.salon.shared.schemas import Moment, PendingOutcome
from app.agents.salon.turn import run as run_module
from app.agents.salon.turn.events import TOOL_LABELS
from app.agents.salon.turn.ledger import TurnLedger
from app.core.clock import TZ, now_utc, to_local
from app.core.config import settings
from app.core.vi_format import format_vi_hhmm
from app.repositories.user import UserRepository
from app.services.appointment import AppointmentService
from app.services.proposal import ProposalService
from app.services.waiting_session import WaitingSessionService
from tests.fakes import Hang, ScriptedChatModel, cite, plain, reply, scripted, tool_call, tool_calls
from tests.salon.subagents import (
    a_user, default_hours, moment, result, seed_mock_catalogue, tomorrow_at, use_model,
)

S15 = moment(tomorrow_at(15))["spoken"]
S16 = moment(tomorrow_at(16))["spoken"]
ISO15 = moment(tomorrow_at(15))["iso"]
ISO16 = moment(tomorrow_at(16))["iso"]
DAY = tomorrow_at(15).date().isoformat()

# Nhãn ngắn `catalog.py::short_label` dựng cho mốc "mai" (Task 2) — chỉ phần
# giờ, dùng làm `find=` để `cite()` tra đúng dòng danh mục dù `S15`/`S16`/...
# ở trên vẫn là câu nói đầy đủ (chữ model "nói" trong kịch bản test).
S15_CLOCK = format_vi_hhmm("15:00")
S16_CLOCK = format_vi_hhmm("16:00")
# Câu mẫu code (câu kế hoạch, câu báo nút bấm) nêu giờ bằng nhãn ngắn.
L15 = f"{S15_CLOCK} mai"
L16 = f"{S16_CLOCK} mai"


# ---------------------------------------------------------------------------
# Model giả
# ---------------------------------------------------------------------------


def _check_history(messages: list[BaseMessage]) -> None:
    """Giả Azure: mỗi tool call cần đúng một ToolMessage ngay sau, không
    ToolMessage lẻ — sai là 400 (CONTEXT.md bẫy 13)."""
    i = 0
    while i < len(messages):
        message = messages[i]
        if isinstance(message, ToolMessage):
            raise ValueError("400 INVALID_TOOL_RESULTS: orphan ToolMessage")
        calls = [*message.tool_calls, *message.invalid_tool_calls] if isinstance(
            message, AIMessage) else []
        if calls:
            expected = sorted(call["id"] for call in calls)
            answered = []
            j = i + 1
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                answered.append(messages[j].tool_call_id)
                j += 1
            if sorted(answered) != expected:
                raise ValueError("400 INVALID_TOOL_RESULTS: tool call without result")
            i = j
            continue
        i += 1


class AzureLike(ScriptedChatModel):
    """`ScriptedChatModel` kiểm lịch sử như Azure ở mỗi lần gọi."""

    def _pop(self, messages):
        _check_history([m for m in messages if not isinstance(m, SystemMessage)])
        return super()._pop(messages)


def orchestrator(monkeypatch, replies) -> AzureLike:
    model = AzureLike(replies=list(replies), calls=[], bind_kwargs=[], tags=["orchestrate"])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda **kwargs: model)
    return model


def subagents(monkeypatch, replies) -> ScriptedChatModel:
    model = scripted(replies, tags=("subagent",))
    use_model(monkeypatch, model)
    return model


@pytest.fixture(autouse=True)
def no_azure(monkeypatch):
    """Model nén và model gom bộ nhớ giả — không bao giờ Azure. Model gom mặc
    định rỗng: gọi tới là lỗi (consolidate tự nuốt lỗi)."""
    summary = scripted([AIMessage("The customer chatted.")] * 40, tags=("summary",))
    monkeypatch.setattr(middleware_pkg, "build_chat_model", lambda **kwargs: summary)
    memory = scripted([], tags=("memory",))
    monkeypatch.setattr(long_term, "build_chat_model", lambda **kwargs: memory)
    return memory


# ---------------------------------------------------------------------------
# Chạy lượt, đọc kết quả
# ---------------------------------------------------------------------------


async def turn(db, user, question) -> list[AgentEvent]:
    return [event async for event in run_turn(db, user, question)]


def completed(events: list[AgentEvent]) -> dict:
    found = [event.data for event in events if event.type == "complete"]
    assert len(found) == 1, [event.type for event in events]
    return found[0]


async def wait_until(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


async def latest_thread_id(db, user) -> str:
    doc = await db["conversation_threads"].find_one(
        {"user_id": str(user.id)}, sort=[("started_at", -1)])
    return str(doc["_id"])


async def thread_messages(db, user, saver=None) -> list[BaseMessage]:
    saver = saver or checkpoint.get_checkpointer()
    found = await saver.aget_tuple({"configurable": {"thread_id": await latest_thread_id(db, user)}})
    return list(found.checkpoint["channel_values"]["messages"])


async def thread_channel_values(db, user, saver=None) -> dict:
    saver = saver or checkpoint.get_checkpointer()
    found = await saver.aget_tuple({"configurable": {"thread_id": await latest_thread_id(db, user)}})
    return dict(found.checkpoint["channel_values"])


def assert_valid_thread(messages: list[BaseMessage]) -> None:
    _check_history(messages)


async def ledger_rows(db, user) -> list[dict]:
    return [doc async for doc in db["salon_turns"].find({"user_id": str(user.id)}).sort("asked_at", 1)]


async def active_sessions(db, user) -> dict:
    return await WaitingSessionService(db).active(str(user.id), now=now_utc())


async def block_15(db) -> None:
    """15:00 mai đã có khách khác đặt."""
    other = await a_user(db, phone="0987654321", name="Chú Ba")
    await AppointmentService(db).create(other, tomorrow_at(15), None)


async def a_proposal(db, user, hour: int = 15):
    proposal = await ProposalService(db).create(user, "book", tomorrow_at(hour), None)
    # `approve` chỉ nhận đề xuất tạo TRƯỚC lượt bấm nút (so mốc mili-giây).
    await asyncio.sleep(0.01)
    return proposal


async def booked_starts(db, user) -> list[datetime]:
    return [to_local(appt.start_at) for appt in await AppointmentService(db).upcoming_for(user)]


# Kịch bản dựng sẵn ---------------------------------------------------------

BOOK_15 = {"request": "Book tomorrow at 3pm.", "continue_session": False, "service_ids": None}
PICK_16 = {"request": "The customer picks 4pm.", "continue_session": True, "service_ids": None}
SHOP = {"request": "Opening hours.", "continue_session": False}

# Câu trả lời cuối của Orchestrator là JSON các đoạn trích dẫn (level C):
# `X` là kịch bản (`reply(...)` / `plain(...)`), `X_TEXT` là chữ đã ghép mà
# khách nhận — các assert so với `X_TEXT`.
ASK_OTHER_TIME_TEXT = f"Dạ {S15} không còn trống ạ. Chị Lan muốn giờ nào khác ạ?"
ASK_OTHER_TIME = reply("Dạ ", cite("taken", S15, find=S15_CLOCK),
                       " không còn trống ạ. Chị Lan muốn giờ nào khác ạ?")
# Khác câu mẫu `PROPOSAL_REPLY` của code — để test phân biệt câu model với câu mẫu.
OFFER_16_TEXT = f"Dạ vâng, em giữ chỗ {S16} cho chị Lan, chị Lan đồng ý không ạ?"
OFFER_16 = reply("Dạ vâng, em giữ chỗ ", cite("held", S16, find=S16_CLOCK),
                 " cho chị Lan, chị Lan đồng ý không ạ?")
OFFER_15_TEXT = f"Dạ vâng, em giữ chỗ {S15} cho chị Lan, chị Lan đồng ý không ạ?"
OFFER_15 = reply("Dạ vâng, em giữ chỗ ", cite("held", S15, find=S15_CLOCK),
                 " cho chị Lan, chị Lan đồng ý không ạ?")
HOURS_TEXT = "Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ."
HOURS = reply("Dạ tiệm mở cửa từ ", cite("hours", "8 giờ sáng"), " tới ", cite("hours", "7 giờ tối"),
              " ạ.")


def need_info_15() -> list[AIMessage]:
    """Agent con: 15:00 kín → need_info, đưa giờ trống gần nhất."""
    return [
        tool_call("parse_time", {"day_offset": 1, "hour": 3, "part_of_day": "chieu"}),
        tool_call("find_free_slots", {"day": DAY, "around": ISO15}),
        result(status="need_info", question_fields=["time"],
               question_text="3pm is taken; ask which free time the customer wants."),
    ]


def propose_16_from_session() -> list[AIMessage]:
    return [
        tool_call("parse_time", {"day_offset": 1, "hour": 4, "part_of_day": "chieu"}),
        tool_call("propose_appointment", {"start_at": ISO16}),
        result(status="ok"),
    ]


def shop_hours() -> list[AIMessage]:
    return [tool_call("get_shop_hours", {}), result(status="ok")]


def two_requests() -> list[AIMessage]:
    """Agent con: đề xuất 15:00, ghi 16:00 là yêu cầu kế (spec §5.1)."""
    return [
        tool_calls(("parse_time", {"day_offset": 1, "hour": 3, "part_of_day": "chieu"}),
                   ("parse_time", {"day_offset": 1, "hour": 4, "part_of_day": "chieu"})),
        tool_call("remember_next_request", {"iso": ISO16}),
        tool_call("propose_appointment", {"start_at": ISO15}),
        result(status="ok"),
    ]


# ---------------------------------------------------------------------------
# Lượt gõ chữ
# ---------------------------------------------------------------------------


async def test_typed_turn_emits_started_tools_and_complete_without_tokens(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [tool_call("shop_info_task", SHOP), HOURS])
    subagents(monkeypatch, shop_hours())

    events = await turn(test_db, user, "tiệm mở cửa mấy giờ vậy em")

    # System prompt cố định trước, khối bối cảnh của lượt là SystemMessage THỨ HAI.
    first_call = model.calls[0]
    assert isinstance(first_call[0], SystemMessage) and first_call[0].content == ORCHESTRATOR_PROMPT
    assert isinstance(first_call[1], SystemMessage) and first_call[1].content.startswith("Today: ")

    types = [event.type for event in events]
    assert types[0] == "turn_started"
    assert types[-1] == "complete"
    assert "token" not in types and "error" not in types
    assert set(types) <= {"turn_started", "tool_started", "tool_finished", "complete"}
    started = [event.data["name"] for event in events if event.type == "tool_started"]
    assert "shop_info_task" in started and "get_shop_hours" in started
    assert "SubagentResult" not in started
    assert completed(events) == {"answer": HOURS_TEXT, "proposal": None}
    [row] = await ledger_rows(test_db, user)
    assert (row["kind"], row["ended_by"], row["answer_source"]) == ("typed", "answer", "llm")
    log = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert [(m["role"], m["content"], m["source"]) for m in log["messages"]] == [
        ("user", "tiệm mở cửa mấy giờ vậy em", "llm"), ("assistant", HOURS_TEXT, "llm")]


async def test_markdown_never_reaches_the_customer(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [tool_call("shop_info_task", SHOP), reply(
        "Dạ tiệm mở cửa từ **", cite("hours", "8 giờ sáng"), " tới ", cite("hours", "7 giờ tối"),
        "** ạ.")])
    subagents(monkeypatch, shop_hours())

    assert completed(await turn(test_db, user, "tiệm mở cửa mấy giờ vậy em"))["answer"] == HOURS_TEXT


async def test_customer_text_is_one_line_and_capped_at_1000_chars(test_db, monkeypatch):
    """Chữ khách gõ thành ĐÚNG MỘT dòng (xuống dòng là lối chèn luật vào prompt)
    và tối đa `CUSTOMER_TEXT_MAX_CHARS` ký tự — cả câu model đọc lẫn nhật ký."""
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [HOURS])
    raw = "tiệm mở\nmấy giờ\r\n\tvậy em  " + "à" * 2000
    expected = " ".join(raw.split())[:run_module.CUSTOMER_TEXT_MAX_CHARS]
    assert run_module.CUSTOMER_TEXT_MAX_CHARS == 1000 and len(expected) == 1000

    completed(await turn(test_db, user, raw))

    humans = [m.content for m in model.calls[0] if isinstance(m, HumanMessage)]
    assert humans and humans[-1] == f"{context_module.UNTRUSTED_LABEL}\n{expected}"
    log = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert log["messages"][0]["content"] == expected


async def test_typed_turn_seeds_self_address_once(test_db, monkeypatch):
    """Ruling R13: khách tự xưng "chị" trong câu gõ → ghi `self_address` một lần."""
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [HOURS, plain("Dạ tiệm mở cả tuần ạ.")])

    completed(await turn(test_db, user, "chị muốn hỏi tiệm mở cửa mấy giờ"))
    assert (await UserRepository(test_db).get_by_id(str(user.id))).self_address == "chị"

    completed(await turn(test_db, user, "anh muốn hỏi tiệm có mở chủ nhật không"))
    assert (await UserRepository(test_db).get_by_id(str(user.id))).self_address == "chị"


async def test_button_turn_does_not_seed_self_address(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [RuntimeError("the model must not be called")])
    proposal = await a_proposal(test_db, user)

    completed(await turn(test_db, user, ButtonReply(str(proposal.id), "no")))

    assert (await UserRepository(test_db).get_by_id(str(user.id))).self_address is None


async def test_inner_tool_events_carry_vietnamese_labels(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [tool_call("appointment_task", BOOK_15), ASK_OTHER_TIME])
    subagents(monkeypatch, need_info_15())

    events = await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")

    started = [event.data for event in events if event.type == "tool_started"]
    finished = [event.data for event in events if event.type == "tool_finished"]
    [slots] = [data for data in started if data["name"] == "find_free_slots"]
    assert slots["label"] == TOOL_LABELS["find_free_slots"]
    assert "find_free_slots" in [data["name"] for data in finished]
    for data in started + finished:
        assert data["label"] and "_" not in data["label"]
        assert data["name"] not in data["label"]
    assert "decide_pending" not in TOOL_LABELS
    assert completed(events)["answer"] == ASK_OTHER_TIME_TEXT


async def test_session_continues_on_the_next_turn(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), ASK_OTHER_TIME,
        tool_call("shop_info_task", SHOP), HOURS,
        tool_call("appointment_task", PICK_16), OFFER_16,
    ])
    sub = subagents(monkeypatch, need_info_15() + shop_hours() + propose_16_from_session())

    first = await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")
    assert completed(first)["proposal"] is None
    assert "appointment" in await active_sessions(test_db, user)

    second = await turn(test_db, user, "tiệm mở cửa mấy giờ vậy em")
    assert completed(second)["answer"] == HOURS_TEXT
    assert "appointment" in await active_sessions(test_db, user)

    third = await turn(test_db, user, "4 giờ đi")
    done = completed(third)
    assert done["answer"] == OFFER_16_TEXT
    assert done["proposal"] is not None and done["proposal"]["when_spoken"] == S16
    call = next(c for c in sub.calls if any(
        isinstance(m, HumanMessage) and "4 giờ đi" in m.content for m in c))
    human = next(m for m in call if isinstance(m, HumanMessage))
    assert "Session memory:" in human.content
    proposal = await ProposalService(test_db).latest(user)
    assert to_local(proposal.start_at) == tomorrow_at(16)
    rows = await ledger_rows(test_db, user)
    assert [(row["ended_by"], row["answer_source"]) for row in rows] == [("answer", "llm")] * 3
    assert any(fact["kind"] == "proposal" for fact in rows[-1]["facts"])


async def test_session_expires_after_ten_minutes(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), ASK_OTHER_TIME,
        tool_call("appointment_task", PICK_16), plain("Dạ chị Lan muốn đặt ngày nào ạ?"),
    ])
    subagents(monkeypatch, need_info_15())
    await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")

    later = now_utc() + timedelta(minutes=settings.salon_session_ttl_minutes + 1)
    monkeypatch.setattr(context_module, "now_utc", lambda: later)
    events = await turn(test_db, user, "4 giờ đi")

    assert completed(events)["answer"] == "Dạ chị Lan muốn đặt ngày nào ạ?"
    block = model.calls[-2][1].content
    assert "Waiting sessions: none" in block
    refused = [m for m in await thread_messages(test_db, user)
               if isinstance(m, ToolMessage) and m.name == "appointment_task"]
    assert json.loads(refused[-1].content)["error_code"] == "no_session"
    assert await ProposalService(test_db).latest(user) is None


async def test_waiting_session_survives_a_restart(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), ASK_OTHER_TIME,
        tool_call("appointment_task", PICK_16), OFFER_16,
    ])
    subagents(monkeypatch, need_info_15() + propose_16_from_session())
    await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")

    checkpoint.set_checkpointer(InMemorySaver())     # như khởi động lại server
    events = await turn(test_db, user, "4 giờ đi")

    done = completed(events)
    assert done["answer"] == OFFER_16_TEXT
    assert done["proposal"]["when_spoken"] == S16
    assert (await ledger_rows(test_db, user))[-1]["answer_source"] == "llm"


# ---------------------------------------------------------------------------
# Nút bấm
# ---------------------------------------------------------------------------


async def test_button_stale_does_not_call_the_model(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [RuntimeError("the model must not be called")])

    events = await turn(test_db, user, ButtonReply(proposal_id="0" * 24, decision="yes"))

    assert completed(events) == {
        "answer": BUTTON_STALE_REPLY.format(address="chị Lan"), "proposal": None}
    assert model.calls == []
    [row] = await ledger_rows(test_db, user)
    assert (row["kind"], row["ended_by"], row["answer_source"]) == (
        "button", "stale_button", "code")


async def test_button_stale_for_an_expired_proposal(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [RuntimeError("the model must not be called")])
    proposal = await a_proposal(test_db, user)
    await test_db["proposals"].update_one(
        {"_id": proposal.id}, {"$set": {"expires_at": now_utc() - timedelta(minutes=1)}})

    events = await turn(test_db, user, ButtonReply(proposal_id=str(proposal.id), decision="yes"))

    assert completed(events)["answer"] == BUTTON_STALE_REPLY.format(address="chị Lan")
    assert model.calls == []
    assert await booked_starts(test_db, user) == []


async def test_button_approve_replies_in_code_without_calling_the_model(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [RuntimeError("the model must not be called")])
    proposal = await a_proposal(test_db, user)

    events = await turn(test_db, user, ButtonReply(proposal_id=str(proposal.id), decision="yes"))

    done = completed(events)
    assert done["answer"] == f"Dạ em đã đặt xong {L15} cho chị Lan ạ."
    assert done["proposal"] is None
    assert model.calls == []
    assert await booked_starts(test_db, user) == [tomorrow_at(15)]
    messages = await thread_messages(test_db, user)
    assert isinstance(messages[-2], HumanMessage) and messages[-2].additional_kwargs["salon_button"]
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].additional_kwargs["salon_source"] == "code"
    assert messages[-1].content == done["answer"]
    [row] = await ledger_rows(test_db, user)
    assert (row["kind"], row["ended_by"], row["answer_source"]) == ("button", "button", "code")
    assert row["decision"]["outcome"] == "approved" and row["announced"] is True
    log = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert [(m["role"], m["content"]) for m in log["messages"]] == [
        ("user", "Đồng ý"), ("assistant", done["answer"])]

    # Lượt gõ chữ sau vẫn chạy trên thread đó.
    model.replies[:] = [plain("Dạ không có gì ạ, hẹn gặp chị Lan.")]
    after = await turn(test_db, user, "cảm ơn em")
    assert completed(after)["answer"] == "Dạ không có gì ạ, hẹn gặp chị Lan."
    assert_valid_thread(await thread_messages(test_db, user))


async def test_button_reject_replies_in_code(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [RuntimeError("the model must not be called")])
    proposal = await a_proposal(test_db, user)

    events = await turn(test_db, user, ButtonReply(proposal_id=str(proposal.id), decision="no"))

    assert completed(events) == {
        "answer": f"Dạ vâng, em bỏ lịch {L15} rồi ạ. Chị Lan muốn giờ khác thì nhắn em nhé.",
        "proposal": None,
    }
    assert model.calls == []
    assert (await ProposalService(test_db).get(user, str(proposal.id))).status == "rejected"
    assert await booked_starts(test_db, user) == []


# Cảnh báo serializer của Pydantic in nguyên SalonContext (có chữ khách) ra stderr.
@pytest.mark.filterwarnings("error:Pydantic serializer warnings")
async def test_second_request_is_proposed_after_the_first_is_approved(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), OFFER_15,
        tool_call("appointment_task", PICK_16), OFFER_16,
    ])
    sub = subagents(monkeypatch, two_requests() + [
        tool_call("propose_appointment", {"start_at": ISO16}), result(status="ok")])

    first = completed(await turn(test_db, user, "đặt cho chị 3 giờ, với cho con gái 4 giờ"))
    assert first["answer"] == OFFER_15_TEXT
    session = (await active_sessions(test_db, user))["appointment"]
    assert [item["iso"] for item in session.memory["next_requests"]] == [ISO16]

    second = completed(await turn(test_db, user, ButtonReply(first["proposal"]["id"], "yes")))
    assert second["answer"] == f"Dạ em đã đặt xong {L15} cho chị Lan ạ. {OFFER_16_TEXT}"
    assert second["proposal"] is not None and second["proposal"]["when_spoken"] == S16
    note = [m for m in model.calls[-2] if isinstance(m, HumanMessage)][-1]
    assert "continue with the next request in the waiting appointment session" in note.content
    calls_before = len(model.calls)
    sub_calls_before = len(sub.calls)

    third = completed(await turn(test_db, user, ButtonReply(second["proposal"]["id"], "yes")))
    assert third["answer"] == f"Dạ em đã đặt xong {L16} cho chị Lan ạ."
    assert third["proposal"] is None
    assert (len(model.calls), len(sub.calls)) == (calls_before, sub_calls_before)
    assert await booked_starts(test_db, user) == [tomorrow_at(15), tomorrow_at(16)]
    assert_valid_thread(await thread_messages(test_db, user))


async def test_button_approve_then_model_error_still_announces_booking(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), OFFER_15,
        RuntimeError("azure is down"),
    ])
    subagents(monkeypatch, two_requests())

    first = completed(await turn(test_db, user, "đặt cho chị 3 giờ, với cho con gái 4 giờ"))
    events = await turn(test_db, user, ButtonReply(first["proposal"]["id"], "yes"))

    done = completed(events)
    assert done == {"answer": f"Dạ em đã đặt xong {L15} cho chị Lan ạ.", "proposal": None}
    assert "appointment" in await active_sessions(test_db, user)
    assert await booked_starts(test_db, user) == [tomorrow_at(15)]
    messages = await thread_messages(test_db, user)
    assert_valid_thread(messages)
    assert messages[-1].content == done["answer"]


async def test_code_fallback_after_a_valid_reply_clears_structured_response(test_db, monkeypatch):
    """Finding 5: the model's node commits `structured_response` to the
    checkpoint BEFORE the guard's `aafter_model` hook runs (langchain
    `agents/factory.py`). If that hook then raises (here: `check_structured`
    itself blows up) instead of returning its usual `_STRUCTURED: None`
    update, `_typed_turn`'s except clause falls back to `_write_code_reply` —
    which must also clear `structured_response`, or a stale parsed reply
    lingers in the checkpoint."""
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [tool_call("appointment_task", BOOK_15), ASK_OTHER_TIME])
    subagents(monkeypatch, need_info_15())

    def _boom(reply_text, facts):
        raise RuntimeError("guard blew up after a valid reply")

    monkeypatch.setattr(reply_guard_module, "check_structured", _boom)

    events = await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")

    answer = completed(events)["answer"]
    assert answer.startswith(TAKEN_REPLY.format(at=L15))
    values = await thread_channel_values(test_db, user)
    assert values.get("structured_response") is None


async def test_superseded_continue_turn_leaves_the_booking_unannounced(test_db, monkeypatch):
    """Ruling R14: lượt nút (duyệt + yêu cầu kế) bị huỷ khi Orchestrator đang
    chạy → câu "đã đặt xong" chưa tới khách → lượt gõ chữ sau phải báo (R0)."""
    await default_hours(test_db)
    user = await a_user(test_db)
    booked_text = f"Dạ em đã đặt xong {S15} cho chị Lan rồi ạ."
    booked = reply("Dạ em đã đặt xong ", cite("booked", S15, find=S15_CLOCK), " cho chị Lan rồi ạ.")
    model = orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), OFFER_15, Hang(), booked])
    subagents(monkeypatch, two_requests())
    first = completed(await turn(test_db, user, "đặt cho chị 3 giờ, với cho con gái 4 giờ"))

    pressed: list = []
    task = _consume(test_db, user, ButtonReply(first["proposal"]["id"], "yes"), pressed)
    await wait_until(lambda: len(model.calls) >= 3)
    after = await turn(test_db, user, "alo em")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert "complete" not in [event.type for event in pressed]
    block = model.calls[-1][1].content
    assert f"Unannounced: approved book at {S15}" in block
    assert completed(after)["answer"] == booked_text
    rows = await ledger_rows(test_db, user)
    assert rows[1]["decision"]["outcome"] == "approved"
    assert rows[1]["announced"] is True          # báo ở lượt 3 (R0), không phải lượt 2


# ---------------------------------------------------------------------------
# Lỗi, trần, thời gian
# ---------------------------------------------------------------------------


async def test_limit_error_gives_a_code_reply_not_english(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    monkeypatch.setattr(settings, "salon_model_calls_per_turn", 2)
    orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15),
        tool_call("shop_info_task", SHOP),
        plain("never reached"),
    ])
    subagents(monkeypatch, need_info_15() + shop_hours())

    events = await turn(test_db, user, "đặt cho chị 3 giờ chiều mai")

    answer = completed(events)["answer"]
    # Câu mẫu báo giờ kín rồi hỏi câu của phiên vừa mở (field "time"), kèm giờ
    # trống, rồi giờ mở cửa agent Thông tin tiệm vừa trả…
    session = (await active_sessions(test_db, user))["appointment"]
    assert answer.startswith(TAKEN_REPLY.format(at=L15))
    assert "chị Lan chọn giờ nào ạ? " in answer
    assert answer.endswith("Dạ tiệm mở cửa từ 8 giờ sáng đến 7 giờ tối ạ.")
    assert session.question.options
    assert all(short_label(Moment(iso=option.iso, spoken=option.spoken), to_local(now_utc()).date())
               in answer for option in session.question.options)
    messages = await thread_messages(test_db, user)
    assert_valid_thread(messages)
    assert messages[-1].content == answer
    assert messages[-1].additional_kwargs["salon_source"] == "code"
    # …nên phiên đó GIỮ mở: lượt sau "4 giờ đi" làm tiếp được (Ruling R12).
    assert "appointment" in await active_sessions(test_db, user)
    [row] = await ledger_rows(test_db, user)
    assert (row["ended_by"], row["answer_source"]) == ("limit", "code")
    assert any(fact["kind"] == "agent_status" for fact in row["facts"])


async def test_limit_without_facts_still_gives_a_code_reply(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    monkeypatch.setattr(settings, "salon_model_calls_per_turn", 1)
    # Tool lạ: không tool nào chạy được, không có dữ kiện nào.
    orchestrator(monkeypatch, [tool_call("no_such_tool", {}), plain("never reached")])

    events = await turn(test_db, user, "alo")

    assert completed(events)["answer"] == GENERIC_FALLBACK.format(address="chị Lan")
    assert_valid_thread(await thread_messages(test_db, user))


async def test_model_call_limit_not_graph_recursion_ends_a_long_turn(test_db, monkeypatch):
    # Với trần mặc định (`salon_model_calls_per_turn`), cái dừng lượt phải là
    # ModelCallLimit (câu mẫu, ended_by="limit"), không phải GraphRecursionError.
    await default_hours(test_db)
    user = await a_user(test_db)
    calls = settings.salon_model_calls_per_turn
    orchestrator(monkeypatch, [tool_call("no_such_tool", {})] * calls + [plain("never reached")])

    events = await turn(test_db, user, "alo")

    assert completed(events)["answer"] == GENERIC_FALLBACK.format(address="chị Lan")
    [row] = await ledger_rows(test_db, user)
    assert (row["ended_by"], row["answer_source"]) == ("limit", "code")


async def test_budget_exhausted_before_any_call_gives_the_turn_error_message(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    monkeypatch.setattr(settings, "orch_llm_calls_per_user_per_hour", 0)
    model = orchestrator(monkeypatch, [plain("never reached")])

    events = await turn(test_db, user, "alo")

    assert [event.type for event in events] == ["turn_started", "error"]
    assert events[-1].data == {"message": TURN_ERROR_MESSAGE}
    assert model.calls == []
    [row] = await ledger_rows(test_db, user)
    assert row["ended_by"] == "error"


async def test_orchestrator_timeout_gives_the_turn_error_message(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    monkeypatch.setattr(settings, "orch_orchestrate_timeout_seconds", 0.3)
    orchestrator(monkeypatch, [Hang(), plain("Dạ em đây ạ.")])

    events = await turn(test_db, user, "alo")

    assert events[-1] == AgentEvent("error", {"message": TURN_ERROR_MESSAGE})
    after = await turn(test_db, user, "alo em")
    assert completed(after)["answer"] == "Dạ em đây ạ."


# ---------------------------------------------------------------------------
# Huỷ / lượt chồng nhau
# ---------------------------------------------------------------------------


def _consume(db, user, question, sink: list) -> asyncio.Task:
    async def _run() -> None:
        async for event in run_turn(db, user, question):
            sink.append(event)
    return asyncio.ensure_future(_run())


async def test_cancelled_turn_leaves_a_thread_the_next_turn_can_use(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), plain("Dạ em đây ạ, chị Lan cần gì ạ?")])
    sub = subagents(monkeypatch, [Hang()])

    events: list = []
    task = _consume(test_db, user, "đặt cho chị 3 giờ chiều mai", events)
    await wait_until(lambda: len(sub.calls) >= 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "complete" not in [event.type for event in events]

    after = await turn(test_db, user, "alo em")
    assert completed(after)["answer"] == "Dạ em đây ạ, chị Lan cần gì ạ?"
    assert_valid_thread(await thread_messages(test_db, user))


async def test_new_turn_supersedes_the_running_one(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [Hang(), plain("Dạ em đây ạ.")])

    first: list = []
    task = _consume(test_db, user, "alo", first)
    await wait_until(lambda: len(model.calls) >= 1)
    second = await turn(test_db, user, "alo em ơi")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert "complete" not in [event.type for event in first]
    assert completed(second)["answer"] == "Dạ em đây ạ."
    messages = await thread_messages(test_db, user)
    assert_valid_thread(messages)
    assert [m.content for m in messages if isinstance(m, HumanMessage)] == ["alo", "alo em ơi"]
    log = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert [m["content"] for m in log["messages"]] == ["alo em ơi", "Dạ em đây ạ."]
    assert run_module._ACTIVE_TURNS == {}


async def test_superseded_turn_closes_the_session_it_opened(test_db, monkeypatch):
    await default_hours(test_db)
    await block_15(test_db)
    user = await a_user(test_db)
    model = orchestrator(monkeypatch, [
        tool_call("appointment_task", BOOK_15), Hang(), plain("Dạ vâng ạ.")])
    subagents(monkeypatch, need_info_15())

    first: list = []
    task = _consume(test_db, user, "đặt cho chị 3 giờ chiều mai", first)
    await wait_until(lambda: len(model.calls) >= 2)
    assert "appointment" in await active_sessions(test_db, user)
    second = await turn(test_db, user, "thôi em")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed(second)["answer"] == "Dạ vâng ạ."
    assert "appointment" not in await active_sessions(test_db, user)
    doc = await test_db["waiting_sessions"].find_one({"user_id": str(user.id)})
    assert doc["closed_reason"] == "superseded"


# ---------------------------------------------------------------------------
# Cuộc trò chuyện mới mỗi ngày + gom bộ nhớ
# ---------------------------------------------------------------------------


def _vn(day, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


async def test_first_turn_of_a_new_conversation_consolidates_memory(
        test_db, monkeypatch, no_azure, memory_store):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        plain("Dạ em chào chị Lan ạ."), plain("Dạ em vẫn ở đây ạ."),
        plain("Dạ chào buổi sáng chị Lan."), plain("Dạ em nghe đây ạ."),
    ])
    no_azure.replies[:] = [
        {"preferences": ["Thích sơn gel màu nhạt"], "notes": [], "last_visit": None},
        RuntimeError("memory model down"),
    ]
    start = to_local(now_utc()).date() - timedelta(days=3)
    clock: dict[str, Optional[datetime]] = {"now": None}
    monkeypatch.setattr(run_module, "now_ms", lambda: clock["now"])

    clock["now"] = _vn(start, 23, 58)
    completed(await turn(test_db, user, "chào em"))
    first_thread = await latest_thread_id(test_db, user)
    clock["now"] = _vn(start + timedelta(days=1), 0, 10)
    completed(await turn(test_db, user, "em ơi"))
    assert await latest_thread_id(test_db, user) == first_thread
    assert no_azure.calls == []

    clock["now"] = _vn(start + timedelta(days=1), 8, 0)
    completed(await turn(test_db, user, "chào buổi sáng"))
    assert await latest_thread_id(test_db, user) != first_thread
    assert len(no_azure.calls) == 1
    stored = await memory_store.aget((MEMORY_NAMESPACE, str(user.id)), "preferences")
    assert stored.value["data"] == ["Thích sơn gel màu nhạt"]

    clock["now"] = _vn(start + timedelta(days=2), 8, 0)
    events = await turn(test_db, user, "alo em")
    assert completed(events)["answer"] == "Dạ em nghe đây ạ."
    assert len(no_azure.calls) == 2


async def test_consolidation_finishes_even_if_its_turn_is_superseded(
        test_db, monkeypatch, no_azure, memory_store):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [plain("Dạ em chào chị Lan ạ."), plain("Dạ em nghe đây ạ.")])
    no_azure.replies[:] = [
        Hang(0.5, then={"preferences": ["Thích sơn gel màu nhạt"], "notes": [], "last_visit": None})]
    start = to_local(now_utc()).date() - timedelta(days=3)
    clock: dict[str, Optional[datetime]] = {"now": _vn(start, 10, 0)}
    monkeypatch.setattr(run_module, "now_ms", lambda: clock["now"])
    completed(await turn(test_db, user, "chào em"))

    clock["now"] = _vn(start + timedelta(days=1), 8, 0)
    first: list = []
    task = _consume(test_db, user, "chào buổi sáng", first)
    await wait_until(lambda: len(no_azure.calls) >= 1)
    clock["now"] = _vn(start + timedelta(days=1), 8, 1)
    completed(await turn(test_db, user, "alo em"))
    with pytest.raises(asyncio.CancelledError):
        await task

    # Gom chạy tiếp ở nền sau khi lượt của nó bị huỷ.
    namespace = (MEMORY_NAMESPACE, str(user.id))
    for _ in range(100):
        item = await memory_store.aget(namespace, "preferences")
        if item is not None:
            break
        await asyncio.sleep(0.05)
    assert item is not None, "consolidation never finished"
    assert item.value["data"] == ["Thích sơn gel màu nhạt"]
    assert len(no_azure.calls) == 1


# ---------------------------------------------------------------------------
# Dịch vụ & giá (Task 17, Ruling R16)
# ---------------------------------------------------------------------------

S9 = moment(tomorrow_at(9))["spoken"]
ISO9 = moment(tomorrow_at(9))["iso"]
S10 = moment(tomorrow_at(10))["spoken"]
S9_CLOCK = format_vi_hhmm("09:00")
S10_CLOCK = format_vi_hhmm("10:00")
OFFER_9_TEXT = f"Dạ vâng, em giữ chỗ {S9} cho chị Lan, chị Lan đồng ý không ạ?"
OFFER_9 = reply("Dạ vâng, em giữ chỗ ", cite("held", S9, find=S9_CLOCK),
                " cho chị Lan, chị Lan đồng ý không ạ?")
DYE_PRICE = {"request": "Price of hair dyeing.", "continue_session": False, "asks_price": True}
DYE = {"request": "Hair dyeing.", "continue_session": False, "asks_price": False}
HAIR_LIST = {"request": "Hair price list.", "continue_session": False, "asks_price": True}
CANCEL_10_TEXT = f"Dạ em sẽ huỷ lịch {S10} cho chị Lan, chị Lan đồng ý không ạ?"
CANCEL_10 = reply("Dạ em sẽ huỷ lịch ", cite("held", S10, find=S10_CLOCK),
                  " cho chị Lan, chị Lan đồng ý không ạ?")
ASK_NAIL_TEXT = "Dạ chị Lan muốn sơn thường hay sơn gel ạ?"
ASK_NAIL = reply("Dạ chị Lan muốn ", cite("service", "sơn thường"), " hay ", cite("service", "sơn gel"),
                 " ạ?")
LISTED_TEXT = "Dạ tiệm có gội sấy, cắt tóc, nhuộm, uốn và hấp dầu ạ."
LISTED = reply("Dạ tiệm có ", cite("service", "gội sấy"), ", ", cite("service", "cắt tóc"), ", ",
               cite("service", "nhuộm"), ", ", cite("service", "uốn"), " và ",
               cite("service", "hấp dầu"), " ạ.")
DYE_PRICE_TEXT = "Dạ nhuộm tóc khoảng 350 nghìn tới 700 nghìn ạ."
DYE_PRICE_REPLY = reply("Dạ ", cite("service", "nhuộm tóc", find="nhuộm"), " ",
                        cite("price", "khoảng 350 nghìn tới 700 nghìn", find="nhuộm"), " ạ.")


def dye_found() -> list[AIMessage]:
    """Agent Dịch vụ: "nhuộm" khớp đúng một dịch vụ → ok (dịch vụ được chốt)."""
    return [tool_call("search_services", {"query": "nhuộm"}), result(status="ok")]


def hair_list() -> list[AIMessage]:
    """Agent Dịch vụ: bảng giá tóc — liệt kê, không chốt dịch vụ nào."""
    return [tool_call("list_services", {"category": "toc"}), result(status="ok")]


def propose_9(**extra) -> list[AIMessage]:
    return [
        tool_call("parse_time", {"day_offset": 1, "hour": 9, "part_of_day": "sang"}),
        tool_call("propose_appointment", {"start_at": ISO9, **extra}),
        result(status="ok"),
    ]


def propose_9_refused(code: str) -> list[AIMessage]:
    """Agent Đặt lịch: `propose_appointment` từ chối vì dịch vụ → `failed` với mã đó."""
    return [
        tool_call("parse_time", {"day_offset": 1, "hour": 9, "part_of_day": "sang"}),
        tool_call("propose_appointment", {"start_at": ISO9}),
        result(status="failed", error_code=code),
    ]


def book_9(service_ids: Optional[list[str]]) -> dict:
    return {"request": "Book tomorrow at 9am.", "continue_session": False,
            "service_ids": service_ids}


def task(request: str) -> dict:
    return {"request": request, "continue_session": False, "service_ids": None}


def sub_results(sub: ScriptedChatModel, name: str) -> list[dict]:
    """JSON mọi ToolMessage `name` agent con nhận được, qua mọi lần chạy."""
    seen: dict[str, dict] = {}
    for call in sub.calls:
        for message in call:
            if isinstance(message, ToolMessage) and message.name == name:
                seen[message.tool_call_id] = json.loads(message.content)
    return list(seen.values())


async def gel_at_10(db, user):
    """Lịch sơn gel (45') của khách lúc 10:00 mai."""
    return await AppointmentService(db).create(user, tomorrow_at(10), None,
                                               service_ids=["nail-son-gel"])


async def test_two_subagents_and_a_rewrite_fit_in_one_turn(test_db, monkeypatch):
    # Live run 1 (11/11), lượt 7: agent Dịch vụ + agent Đặt lịch + guard bắt viết
    # lại = 4 lần gọi model, ~28 bước graph > 25 mặc định của langchain-core →
    # GraphRecursionError dù còn xa trần `salon_model_calls_per_turn`.
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    # Thiếu câu hỏi đồng ý → `missing_question`, viết lại một lần.
    no_question = reply("Dạ em giữ chỗ ", cite("held", S9, find=S9_CLOCK), " cho chị Lan ạ.")
    orchestrator(monkeypatch, [
        tool_call("service_task", DYE), tool_call("appointment_task", book_9(["toc-nhuom"])),
        no_question, OFFER_9,
    ])
    subagents(monkeypatch, dye_found() + propose_9())

    done = completed(await turn(test_db, user, "chị muốn đặt nhuộm tóc sáng mai lúc 9 giờ"))

    assert done["answer"] == OFFER_9_TEXT
    [row] = await ledger_rows(test_db, user)
    assert (row["ended_by"], row["answer_source"]) == ("answer", "llm")


async def test_service_known_earlier_is_reusable(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("service_task", DYE_PRICE), DYE_PRICE_REPLY,
        # Lượt 2: quên mang dịch vụ đã chốt → agent Đặt lịch `failed` service_required,
        # Orchestrator gọi lại kèm id.
        tool_call("appointment_task", book_9(None)),
        tool_call("appointment_task", book_9(["toc-nhuom"])), OFFER_9,
    ])
    sub = subagents(monkeypatch, dye_found() + propose_9_refused("service_required") + propose_9())

    assert completed(await turn(test_db, user, "nhuộm tóc giá bao nhiêu em"))["answer"] == DYE_PRICE_TEXT
    done = completed(await turn(test_db, user, "đặt cho chị 9 giờ sáng mai"))

    assert done["answer"] == OFFER_9_TEXT
    assert done["proposal"]["when_spoken"] == S9
    refused, made = sub_results(sub, "propose_appointment")
    assert refused["error"] == "service_required" and "proposal_id" in made
    proposal = await ProposalService(test_db).latest(user)
    assert (proposal.service_ids, proposal.duration_minutes) == (["toc-nhuom"], 120)


def haircut_price(amount: str):
    return reply("Dạ ", cite("service", "cắt tóc nữ"), " ",
                 cite("price", amount, find="cắt tóc nữ"), " ạ.")


# 200k nằm ngoài khoảng giá → `ref_mismatch` (trước level C: `unknown_price`).
@pytest.mark.parametrize("amount, rewritten", [("khoảng 150 nghìn", False), ("200k", True)])
async def test_price_question_is_answered_from_the_catalogue(test_db, monkeypatch, amount, rewritten):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    said = f"Dạ cắt tóc nữ {amount} ạ."
    good = "Dạ cắt tóc nữ từ 100 nghìn tới 150 nghìn ạ."
    model = orchestrator(monkeypatch, [
        tool_call("service_task", {"request": "Price of a women's haircut.",
                                   "continue_session": False, "asks_price": True}),
        haircut_price(amount), haircut_price("từ 100 nghìn tới 150 nghìn"),
    ])
    subagents(monkeypatch, [tool_call("search_services", {"query": "cắt tóc nữ"}),
                            result(status="ok")])

    done = completed(await turn(test_db, user, "cắt tóc nữ giá bao nhiêu em"))

    assert done["answer"] == (good if rewritten else said)
    assert len(model.calls) == (3 if rewritten else 2)
    [row] = await ledger_rows(test_db, user)
    assert row["answer_source"] == "llm"
    assert [f["service_id"] for f in row["facts"] if f["kind"] == "service"] == ["toc-cat-nu"]


async def test_dye_booking_proposes_a_two_hour_slot(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("service_task", DYE),
        tool_call("appointment_task", book_9(["toc-nhuom"])), OFFER_9,
    ])
    subagents(monkeypatch, dye_found() + propose_9())

    events = await turn(test_db, user, "nhuộm tóc cho chị 9 giờ sáng mai")

    done = completed(events)
    assert done["answer"] == OFFER_9_TEXT
    assert done["proposal"]["when_spoken"] == S9
    proposal = await ProposalService(test_db).latest(user)
    assert (proposal.service_ids, proposal.duration_minutes) == (["toc-nhuom"], 120)
    started = [event.data for event in events if event.type == "tool_started"]
    labels = {data["name"]: data["label"] for data in started}
    assert labels["service_task"] == TOOL_LABELS["service_task"]
    assert labels["search_services"] == TOOL_LABELS["search_services"]


async def test_named_but_unclear_service_is_asked_not_proposed(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("service_task", {"request": "Nail service.", "continue_session": False,
                                   "asks_price": False}),
        tool_call("appointment_task", book_9(None)),
        ASK_NAIL,
    ])
    sub = subagents(monkeypatch, [
        tool_call("search_services", {"query": "sơn móng"}),
        result(status="need_info", question_fields=["service"],
               question_text="Ask whether the customer wants regular polish or gel polish."),
        *propose_9_refused("service_unclear"),
    ])

    done = completed(await turn(test_db, user, "làm móng cho chị 9 giờ sáng mai"))

    assert done == {"answer": ASK_NAIL_TEXT, "proposal": None}
    assert [r["error"] for r in sub_results(sub, "propose_appointment")] == ["service_unclear"]
    assert await ProposalService(test_db).latest(user) is None
    session = (await active_sessions(test_db, user))["service"]
    assert session.memory["candidates"] == ["nail-son-thuong", "nail-son-gel"]


async def test_move_after_a_price_question_keeps_its_length(test_db, monkeypatch):
    """Ruling R16: hỏi giá nhuộm rồi dời lịch sơn gel → vẫn 45', dịch vụ giữ nguyên."""
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    appt = await gel_at_10(test_db, user)
    model = orchestrator(monkeypatch, [
        tool_call("service_task", DYE_PRICE), DYE_PRICE_REPLY,
        tool_call("appointment_task", task("Move the 10am appointment to 9am tomorrow.")),
        OFFER_9,
    ])
    subagents(monkeypatch, dye_found() + propose_9(replaces_appointment_id=str(appt.id)))

    completed(await turn(test_db, user, "nhuộm tóc giá bao nhiêu em"))
    done = completed(await turn(test_db, user, "dời lịch mai sang 9 giờ sáng giúp chị"))

    assert done["answer"] == OFFER_9_TEXT
    # Bối cảnh đưa id dịch vụ của lịch (dời THÊM dịch vụ thì truyền đủ id cũ + mới).
    assert f"- {appt.id}: {S10} " in model.calls[2][1].content
    assert "services=[nail-son-gel]" in model.calls[2][1].content
    proposal = await ProposalService(test_db).latest(user)
    assert (proposal.kind, proposal.duration_minutes, proposal.service_ids) == ("move", 45, [])

    completed(await turn(test_db, user, ButtonReply(str(proposal.id), "yes")))
    [moved] = await AppointmentService(test_db).upcoming_for(user)
    assert to_local(moved.start_at) == tomorrow_at(9)
    assert (moved.duration_minutes, moved.service_ids) == (45, ["nail-son-gel"])


async def test_list_and_cancel_after_a_price_list_are_allowed(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    appt = await gel_at_10(test_db, user)
    listed_text = f"Dạ chị Lan có lịch {S10} ạ."
    listed = reply("Dạ chị Lan có lịch ", cite("appointment", S10, find=S10_CLOCK), " ạ.")
    orchestrator(monkeypatch, [
        tool_call("service_task", HAIR_LIST), LISTED,
        tool_call("appointment_task", task("List the customer's appointments.")), listed,
        tool_call("appointment_task", task("Cancel the 10am appointment tomorrow.")),
        CANCEL_10,
    ])
    subagents(monkeypatch, hair_list() + [
        tool_call("list_my_appointments", {}), result(status="ok"),
        tool_call("propose_cancel", {"appointment_id": str(appt.id)}), result(status="ok"),
    ])

    assert completed(await turn(test_db, user, "bảng giá làm tóc"))["answer"] == LISTED_TEXT
    assert completed(await turn(test_db, user, "lịch của chị khi nào em"))["answer"] == listed_text
    done = completed(await turn(test_db, user, "huỷ lịch đó giúp chị"))

    assert done["answer"] == CANCEL_10_TEXT
    assert done["proposal"]["kind"] == "cancel"


async def test_booking_after_a_price_list_uses_the_default_length(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [
        tool_call("service_task", HAIR_LIST), LISTED,
        tool_call("appointment_task", book_9(None)), OFFER_9,
    ])
    subagents(monkeypatch, hair_list() + propose_9())

    completed(await turn(test_db, user, "bảng giá làm tóc"))
    done = completed(await turn(test_db, user, "đặt cho chị 9 giờ sáng mai"))

    assert done["answer"] == OFFER_9_TEXT
    proposal = await ProposalService(test_db).latest(user)
    assert (proposal.duration_minutes, proposal.service_ids) == (settings.booking_slot_minutes, [])


async def test_cancel_while_a_service_session_waits(test_db, monkeypatch):
    await default_hours(test_db)
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    appt = await gel_at_10(test_db, user)
    orchestrator(monkeypatch, [
        tool_call("service_task", {"request": "Nail service.", "continue_session": False,
                                   "asks_price": False}),
        ASK_NAIL,
        tool_call("appointment_task", task("Cancel the 10am appointment tomorrow.")),
        CANCEL_10,
    ])
    subagents(monkeypatch, [
        tool_call("search_services", {"query": "sơn móng"}),
        result(status="need_info", question_fields=["service"], question_text="Regular or gel?"),
        tool_call("propose_cancel", {"appointment_id": str(appt.id)}), result(status="ok"),
    ])

    completed(await turn(test_db, user, "làm móng cho chị"))
    assert "service" in await active_sessions(test_db, user)
    done = completed(await turn(test_db, user, "thôi huỷ lịch 10 giờ mai giúp chị"))

    assert done["answer"] == CANCEL_10_TEXT
    assert done["proposal"]["kind"] == "cancel"


# ---------------------------------------------------------------------------
# Level C: đánh dấu "đã báo" theo đoạn trích dẫn (Task 7)
# ---------------------------------------------------------------------------

APPROVE = {"label": "approve", "aside": None, "reason": "The customer agrees."}


async def seed_r0(db, user, hour: int = 15) -> str:
    """Quyết định đã duyệt của một lượt trước, chưa báo khách (R0)."""
    ledger = TurnLedger(db)
    turn_id = "r0-turn"
    await ledger.start(turn_id=turn_id, user_id=str(user.id), thread_id="old-thread",
                       asked_at=now_utc() - timedelta(minutes=1), kind="typed")
    await ledger.set_decision(turn_id, PendingOutcome(
        outcome="approved", proposal_id="p-r0", kind="book", at=Moment(**moment(tomorrow_at(hour)))))
    return turn_id


async def test_short_phrased_decision_is_marked_announced(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    await a_proposal(test_db, user, hour=9)
    orchestrator(monkeypatch, [
        tool_call("decide_pending", APPROVE),
        reply("Dạ em đã đặt xong ", cite("booked", "9 giờ sáng mai", find="9 giờ sáng"),
              " cho chị Lan rồi ạ."),
    ])

    done = completed(await turn(test_db, user, "đồng ý em"))

    assert done["answer"] == "Dạ em đã đặt xong 9 giờ sáng mai cho chị Lan rồi ạ."
    [row] = await ledger_rows(test_db, user)
    assert row["decision"]["outcome"] == "approved"
    assert (await TurnLedger(test_db).record(row["turn_id"])).announced is True


async def test_r0_cited_in_a_short_phrase_marks_the_r0_turn(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    r0_turn = await seed_r0(test_db, user)
    orchestrator(monkeypatch, [
        reply("Dạ em đã đặt xong ", cite("booked", "3 giờ chiều mai", find="3 giờ chiều"),
              " cho chị Lan rồi ạ."),
    ])

    done = completed(await turn(test_db, user, "alo em"))

    assert done["answer"] == "Dạ em đã đặt xong 3 giờ chiều mai cho chị Lan rồi ạ."
    assert (await TurnLedger(test_db).record(r0_turn)).announced is True


async def test_two_bad_structures_end_the_turn_on_the_template(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [AIMessage("Dạ có ạ."), AIMessage("Dạ vâng.")])

    events = await turn(test_db, user, "alo em")

    assert "error" not in [event.type for event in events]
    assert completed(events)["answer"] == GENERIC_FALLBACK.format(address="chị Lan")
    [row] = await ledger_rows(test_db, user)
    assert (row["ended_by"], row["answer_source"]) == ("fallback", "code")


async def test_code_template_still_marks_announced_verbatim(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    r0_turn = await seed_r0(test_db, user)
    orchestrator(monkeypatch, [AIMessage("Dạ có ạ."), AIMessage("Dạ vâng.")])

    answer = completed(await turn(test_db, user, "alo em"))["answer"]

    assert L15 in answer
    assert (await TurnLedger(test_db).record(r0_turn)).announced is True
