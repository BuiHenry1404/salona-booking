"""Node `confirm` (M3): interrupt → lượt sau code quyết → `ProposalService.approve`.

Hai phần:
- `execute_proposal`: mọi khẳng định nghiệp vụ của node regex cũ (giờ lấy từ
  DB, xưng hô suy từ tên, giờ đã có người, dời không thêm lịch, hủy, lịch đã
  mất, kết quả hỏng) — nay đi từ một đề xuất trong `proposals`.
- node trong một graph một-node có `InMemorySaver`: interrupt, resume, phủ
  quyết, hết hạn, hỏi lại khi không rõ.
"""
import json
from datetime import datetime, timedelta

import pytest
from bson import ObjectId
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.booking_graph import reply
from app.agents.booking_graph.confirm import (PROPOSAL_MAX_AGE_MINUTES,
                                              _sentence_start, clarify_question,
                                              execute_proposal, make_confirm_node)
from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.tools import make_booking_tools
from app.core.clock import TZ, now_utc
from app.services import proposal as proposal_module
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import ProposalService

pytestmark = pytest.mark.asyncio


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def later():
    return now_utc() + timedelta(seconds=5)


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


async def book_proposal(db, user, hour=15, note="làm tóc"):
    return str((await ProposalService(db).create(user, "book", tomorrow_at(hour), note)).id)


async def test_proposals_still_expire_after_ten_minutes():
    assert PROPOSAL_MAX_AGE_MINUTES == 10


# --- execute_proposal: nghiệp vụ ---------------------------------------------

async def test_propose_then_approve_books_the_time_from_the_db(test_db):
    """Nối hai đầu: `propose_appointment` tạo đề xuất, `confirm` duyệt bằng id.

    Giá trị đem đi ghi lịch lấy từ DB, KHÔNG từ chuỗi model gõ lại ở lượt sau.
    Model chép sai 15:00 thành 5:00 cũng không ảnh hưởng.
    """
    user = await a_user(test_db)
    propose = next(t for t in make_booking_tools(test_db, user) if t.name == "propose_appointment")
    pid = json.loads(await propose.ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}))["proposal_id"]

    out = await execute_proposal(test_db, user, pid, turn_at=later())

    assert out["confirm_fact"]["kind"] == "booked"
    assert "xong" in out["fallback"].lower()
    booked = await AppointmentService(test_db).upcoming_for(user)
    assert len(booked) == 1
    assert booked[0].start_at.astimezone(TZ) == tomorrow_at(15)
    assert booked[0].note == "làm tóc"


async def test_confirm_returns_fact_and_fallback_not_answer(test_db):
    user = await a_user(test_db)
    out = await execute_proposal(test_db, user, await book_proposal(test_db, user), turn_at=later())
    assert "answer" not in out
    assert out["confirm_fact"] == {"kind": "booked", "when": format_vi_datetime(tomorrow_at(15)),
                                   "note": "làm tóc", "address": "chị Lan", "error": None}
    assert out["fallback"].startswith("Xong rồi ạ")


async def test_taken_slot_produces_a_friendly_message_not_a_crash(test_db):
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    other = await a_user(test_db, "0938111222", "Cô Hoa")
    await AppointmentService(test_db).create(other, tomorrow_at(15), note=None)

    out = await execute_proposal(test_db, user, pid, turn_at=later())

    assert out["confirm_fact"]["kind"] == "failed"
    assert "có người" in out["fallback"].lower()


async def test_not_approvable_gives_nothing_to_confirm(test_db):
    lan = await a_user(test_db)
    hoa = await a_user(test_db, "0938111222", "Cô Hoa")
    pid = await book_proposal(test_db, lan)
    assert await execute_proposal(test_db, hoa, pid, turn_at=later()) is None
    assert await test_db["appointments"].count_documents({}) == 0


async def test_a_broken_proposal_result_gets_the_em_nham_answer(test_db):
    """Đã duyệt nhưng lịch không đọc lại được (thay cho nhánh `bad_pending_payload`)."""
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    await execute_proposal(test_db, user, pid, turn_at=later())
    await test_db["appointments"].delete_many({})

    out = await execute_proposal(test_db, user, pid, turn_at=later())

    assert out["confirm_fact"]["kind"] == "failed"
    assert "em nhầm mất rồi" in out["fallback"]


class TestConfirmUsesTheRightHonorific:
    """Câu chốt phải gọi khách đúng như lúc hỏi xác nhận. Xưng hô suy bằng code từ
    `full_name` ("Chú Ba" → "anh Ba"); prompt cấm "cô", "chú", "bác"."""

    async def test_the_honorific_is_derived_from_the_customer_name(self, test_db):
        user = await a_user(test_db, name="Chú Ba")
        out = await execute_proposal(test_db, user, await book_proposal(test_db, user, note="cắt tóc"),
                                     turn_at=later())
        assert "anh Ba" in out["fallback"]
        assert "chú" not in out["fallback"].lower()
        assert "cô chú" not in out["fallback"]

    async def test_without_a_derivable_honorific_it_does_not_invent_one(self, test_db):
        user = await a_user(test_db, name="Nguyễn Thị Lan")
        out = await execute_proposal(test_db, user, await book_proposal(test_db, user, note=None),
                                     turn_at=later())
        assert "cô chú" not in out["fallback"]
        assert "Xong rồi ạ" in out["fallback"]

    async def test_the_booking_time_is_still_in_the_sentence(self, test_db):
        user = await a_user(test_db, name="Bùi Văn Ba")
        out = await execute_proposal(test_db, user, await book_proposal(test_db, user, note=None),
                                     turn_at=later())
        assert "3 giờ chiều" in out["fallback"]

    async def test_a_taken_slot_keeps_the_honorific(self, test_db):
        user = await a_user(test_db, name="Chú Ba")
        pid = await book_proposal(test_db, user)
        other = await a_user(test_db, "0999888777", "Người khác")
        await AppointmentService(test_db).create(other, tomorrow_at(15), "làm tóc")
        out = await execute_proposal(test_db, user, pid, turn_at=later())
        assert "cô chú" not in out["fallback"].lower(), out["fallback"]
        assert "Anh Ba chọn giờ khác" in out["fallback"]


class TestConfirmReschedules:
    """BUG-1: đề xuất `move` phải dời lịch — không đặt thêm (bẫy #19)."""

    async def test_approve_moves_instead_of_adding(self, test_db):
        user = await a_user(test_db, name="Chú Hùng")
        appts = AppointmentService(test_db)
        old = await appts.create(user, tomorrow_at(9), "làm tóc")
        p = await ProposalService(test_db).create(user, "move", tomorrow_at(10), None, target=old)

        out = await execute_proposal(test_db, user, str(p.id), turn_at=later())

        upcoming = await appts.upcoming_for(user)
        assert [a.start_at.astimezone(TZ) for a in upcoming] == [tomorrow_at(10)]
        assert upcoming[0].note == "làm tóc"
        assert "dời" in out["fallback"].lower()
        assert "anh Hùng" in out["fallback"]
        assert "10 giờ sáng" in out["fallback"]
        assert out["confirm_fact"]["kind"] == "moved"

    async def test_a_taken_new_time_keeps_the_old_appointment(self, test_db):
        user = await a_user(test_db)
        other = await a_user(test_db, "0938111222", "Cô Hoa")
        appts = AppointmentService(test_db)
        old = await appts.create(user, tomorrow_at(9), "làm tóc")
        p = await ProposalService(test_db).create(user, "move", tomorrow_at(10), None, target=old)
        await appts.create(other, tomorrow_at(10), None)

        out = await execute_proposal(test_db, user, str(p.id), turn_at=later())

        assert "có người" in out["fallback"].lower()
        upcoming = await appts.upcoming_for(user)
        assert [a.start_at.astimezone(TZ) for a in upcoming] == [tomorrow_at(9)]


class TestConfirmCancels:
    """Hủy cũng qua bước xác nhận: đề xuất `cancel`, duyệt mới hủy thật."""

    async def _cancel_proposal(self, db, user, appt):
        return str((await ProposalService(db).create(user, "cancel", appt.start_at, None,
                                                     target=appt)).id)

    async def test_approve_cancels_and_says_so(self, test_db):
        user = await a_user(test_db, name="Chú Hùng")
        appt = await AppointmentService(test_db).create(user, tomorrow_at(15), "làm tóc")

        out = await execute_proposal(test_db, user, await self._cancel_proposal(test_db, user, appt),
                                     turn_at=later())

        assert await AppointmentService(test_db).upcoming_for(user) == []
        assert "hủy" in out["fallback"].lower()
        assert "anh Hùng" in out["fallback"] and "3 giờ chiều" in out["fallback"]
        assert out["confirm_fact"]["kind"] == "cancelled"

    async def test_already_gone_appointment_gets_a_friendly_answer(self, test_db):
        user = await a_user(test_db)
        appt = await AppointmentService(test_db).create(user, tomorrow_at(15), "làm tóc")
        pid = await self._cancel_proposal(test_db, user, appt)
        await AppointmentService(test_db).cancel(user, str(appt.id))

        out = await execute_proposal(test_db, user, pid, turn_at=later())
        assert "không tìm thấy" in out["fallback"].lower()


async def test_sentence_start_keeps_the_name_capitalised():
    """str.capitalize() hạ chữ tên: "anh Hùng" -> "Anh hùng"."""
    assert _sentence_start("anh Hùng") == "Anh Hùng"
    assert _sentence_start("anh chị") == "Anh chị"


async def test_clarify_question_names_the_time_and_the_customer():
    q = clarify_question("book", "Thứ Ba 20/10, 3 giờ chiều", "chị Lan")
    assert q == "Dạ chị Lan chốt giúp em lịch Thứ Ba 20/10, 3 giờ chiều nha, đúng không ạ?"
    assert "hủy" in clarify_question("cancel", "x", "anh Ba")
    assert "dời" in clarify_question("move", "x", "anh Ba")


# --- node trong graph một-node có checkpointer ---------------------------------

QUESTION = "Dạ em giữ chỗ 3 giờ chiều mai cho chị Lan, chị chốt giúp em nhé?"
CFG = {"configurable": {"thread_id": "t1"}}


@pytest.fixture
def classifier(monkeypatch):
    """Bộ phân loại giả: trả nhãn đặt sẵn, đếm số lần được gọi."""
    state = {"label": "approve", "calls": 0}

    async def fake(proposal, bot_question, customer_text):
        state["calls"] += 1
        state["question"] = bot_question
        return state["label"]

    monkeypatch.setattr(reply, "classify_reply", fake)
    return state


def one_node_graph(db, user):
    g = StateGraph(GraphState)
    g.add_node("confirm", make_confirm_node(db, user))
    g.add_edge(START, "confirm")
    g.add_edge("confirm", END)
    return g.compile(checkpointer=InMemorySaver())


async def ask(graph, pid):
    await graph.ainvoke({"messages": [HumanMessage("mai 3 giờ chiều chị làm tóc"),
                                      AIMessage(QUESTION)], "proposal_id": pid}, CFG)
    return await graph.aget_state(CFG)


async def answer(graph, text):
    await graph.ainvoke(Command(resume=text, update={
        "messages": [HumanMessage(text)], "asked_at": later()}), CFG)
    return (await graph.aget_state(CFG)).values


async def status_of(db, pid):
    return (await db["proposals"].find_one({"_id": ObjectId(pid)}))["status"]


async def test_no_proposal_ends(test_db):
    user = await a_user(test_db)
    out = await make_confirm_node(test_db, user)({"messages": [], "proposal_id": None})
    assert out == {"gate_next": "end"}


async def test_confirm_interrupts_with_the_proposal(test_db, classifier):
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)

    snap = await ask(one_node_graph(test_db, user), pid)

    assert [i.value for i in snap.interrupts] == [{"proposal_id": pid, "question": QUESTION}]
    assert classifier["calls"] == 0
    assert await test_db["appointments"].count_documents({}) == 0


async def test_resume_with_approve_writes_once(test_db, classifier):
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, "ừ em")

    assert values["gate_next"] == "phrase" and values["proposal_id"] is None
    assert values["confirm_fact"]["kind"] == "booked"
    assert classifier["question"] == QUESTION           # model thấy đúng câu bot vừa hỏi
    assert await test_db["appointments"].count_documents({}) == 1
    assert await status_of(test_db, pid) == "approved"


@pytest.mark.parametrize("text", ["dạ ko em", "da ko em", "dạ khoan", "để chị hỏi thằng Huy đã",
                                  "dạ 4 giờ nha em", "dạ vâng để chị gọi"])
async def test_resume_with_veto_writes_nothing_and_routes_booking(test_db, classifier, text):
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, text)

    assert values["gate_next"] == "booking" and values["proposal_id"] is None
    assert classifier["calls"] == 0                     # lưới chặn trước model
    assert await test_db["appointments"].count_documents({}) == 0
    assert await status_of(test_db, pid) == "rejected"


async def test_expired_proposal_routes_to_supervisor_without_writing(test_db, classifier, monkeypatch):
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    future = now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES + 1)
    monkeypatch.setattr(proposal_module, "now_utc", lambda: future)
    values = await answer(graph, "ừ")

    assert values["gate_next"] == "supervisor" and values["proposal_id"] is None
    assert classifier["calls"] == 0
    assert await test_db["appointments"].count_documents({}) == 0
    assert await status_of(test_db, pid) == "expired"


async def test_change_supersedes_and_routes_booking(test_db, classifier):
    classifier["label"] = "change"
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, "cho chị làm thêm móng nữa")

    assert values["gate_next"] == "booking"
    assert await status_of(test_db, pid) == "superseded"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_first_unclear_asks_once_and_keeps_the_proposal(test_db, classifier):
    classifier["label"] = "unclear"
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, "cho cô dùng thử")

    # "phrase": agent booking đặt nguyên văn câu hỏi lại (PhraseMiddleware), guard kiểm.
    assert values["gate_next"] == "phrase"
    assert values["proposal_id"] == pid                  # vẫn chờ đề xuất này
    assert values["draft"] == values["fallback"] == clarify_question(
        "book", format_vi_datetime(tomorrow_at(15)), "chị Lan")
    assert values["confirm_fact"]["kind"] == "clarify"
    assert await status_of(test_db, pid) == "pending"
    assert await test_db["appointments"].count_documents({}) == 0


async def test_second_unclear_rejects_and_routes_booking(test_db, classifier):
    classifier["label"] = "unclear"
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    await ProposalService(test_db).mark_unclear(user, pid)        # lần không rõ thứ nhất
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, "cho cô dùng thử")

    assert values["gate_next"] == "booking" and values["proposal_id"] is None
    assert await status_of(test_db, pid) == "rejected"


async def test_huy_di_approves_a_cancel_proposal(test_db, classifier):
    user = await a_user(test_db)
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), "làm tóc")
    pid = str((await ProposalService(test_db).create(user, "cancel", appt.start_at, None,
                                                     target=appt)).id)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    values = await answer(graph, "ừ hủy đi em")

    assert values["gate_next"] == "phrase"
    assert values["confirm_fact"]["kind"] == "cancelled"
    assert await AppointmentService(test_db).upcoming_for(user) == []


async def test_another_customers_proposal_id_in_state_never_writes(test_db, classifier):
    lan = await a_user(test_db)
    hoa = await a_user(test_db, "0938111222", "Cô Hoa")
    pid = await book_proposal(test_db, lan)
    graph = one_node_graph(test_db, hoa)
    await ask(graph, pid)

    values = await answer(graph, "ừ")

    assert values["gate_next"] == "supervisor"
    assert await test_db["appointments"].count_documents({}) == 0
    assert await status_of(test_db, pid) == "pending"


async def test_resume_after_an_already_approved_proposal_confirms_without_writing_again(
        test_db, classifier):
    """F22 + review M3: lượt resume trước bị huỷ SAU khi đã ghi — interrupt vẫn
    treo. Lần resume sau thấy đề xuất `approved`: duyệt lại idempotent (cùng một
    lịch) và đi đường chốt, để khách nghe xác nhận cho lịch có thật."""
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)
    first = await ProposalService(test_db).approve(user, pid, turn_at=later())

    values = await answer(graph, "ừ ạ")

    assert values["gate_next"] == "phrase"
    assert values["confirm_fact"]["kind"] == "booked"
    assert values["proposal_id"] is None
    assert classifier["calls"] == 0
    assert await test_db["appointments"].count_documents({}) == 1
    assert str(first.appointment.id) == str(
        (await test_db["proposals"].find_one({"_id": ObjectId(pid)}))["result_appointment_id"])


async def test_resume_waits_for_an_approval_still_running(test_db, classifier, monkeypatch):
    """Đề xuất còn `approving` (việc duyệt shield của lượt bị huỷ chưa xong):
    node chờ ngắn rồi đọc lại, thấy `approved` → câu chốt."""
    import asyncio
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)
    await test_db["proposals"].update_one({"_id": ObjectId(pid)}, {"$set": {
        "status": "approving", "approving_at": now_utc()}})

    async def finish_later():
        await asyncio.sleep(0.4)
        appt = await AppointmentService(test_db).create(user, tomorrow_at(15), "làm tóc")
        await test_db["proposals"].update_one({"_id": ObjectId(pid)}, {"$set": {
            "status": "approved", "result_appointment_id": str(appt.id)}})

    task = asyncio.create_task(finish_later())
    values = await answer(graph, "ừ ạ")
    await task

    assert values["gate_next"] == "phrase"
    assert values["confirm_fact"]["kind"] == "booked"
    assert await test_db["appointments"].count_documents({}) == 1


async def test_a_cancelled_resume_leaves_the_interrupt_waiting():
    """Hợp đồng thư viện mà cách sửa dựa vào (probe của review): resume bị huỷ
    giữa chừng thì interrupt VẪN treo, lần resume sau chạy lại node từ đầu."""
    import asyncio
    from typing import TypedDict

    from langgraph.types import interrupt

    log = []

    class S(TypedDict, total=False):
        x: str

    async def node(s):
        v = interrupt({"q": 1})
        log.append(("resumed_with", v))
        await asyncio.sleep(0.5)
        log.append(("finished", v))
        return {"x": v}

    g = StateGraph(S)
    g.add_node("c", node)
    g.add_edge(START, "c")
    g.add_edge("c", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}

    async def run(inp):
        async for _ in app.astream_events(inp, config=cfg, version="v2"):
            pass

    await run({"x": ""})
    task = asyncio.create_task(run(Command(resume="A")))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await app.aget_state(cfg)).interrupts            # vẫn chờ
    await run(Command(resume="B"))
    assert log == [("resumed_with", "A"), ("resumed_with", "B"), ("finished", "B")]
    assert (await app.aget_state(cfg)).values["x"] == "B"


async def test_an_approval_outliving_the_wait_is_logged_and_writes_nothing_here(
        test_db, classifier, monkeypatch):
    from app.agents.booking_graph import confirm as confirm_module
    user = await a_user(test_db)
    pid = await book_proposal(test_db, user)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)
    await test_db["proposals"].update_one({"_id": ObjectId(pid)}, {"$set": {
        "status": "approving", "approving_at": now_utc()}})
    monkeypatch.setattr(confirm_module, "APPROVING_WAIT_SECONDS", 0.2)
    logged = []
    monkeypatch.setattr(confirm_module.logger, "warning", lambda event, **kw: logged.append(event))

    values = await answer(graph, "ừ")

    assert "approval_still_running" in logged
    assert values["gate_next"] == "supervisor"
    assert classifier["calls"] == 0
    assert await test_db["appointments"].count_documents({}) == 0
