"""Đầu-cuối qua `run_turn` (M3): lượt 1 đề xuất + hỏi xác nhận → interrupt; lượt 2
resume bằng câu khách → code quyết.

Graph thật, model giả cho supervisor/agent/phrase, bộ phân loại giả (thường là
LUÔN approve — để chứng minh lưới code tự chặn được các câu đã làm A ghi sai).
DB `chatbot_test_db`; checkpointer `InMemorySaver` (conftest), trừ một test chạy
trên `MongoDBSaver` thật.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents import checkpoint
from app.agents.booking_graph import agents, events, phrase, reply, rewrite, supervisor
from app.agents.booking_graph.context import format_vi_datetime
from app.core.clock import TZ, now_utc
from app.services import proposal as proposal_module
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import PROPOSAL_MAX_AGE_MINUTES, ProposalService
from tests.fakes import scripted, tool_call

TAN_GAU = "Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc"
QUESTION = "Dạ em giữ chỗ 3 giờ chiều mai cho chị Lan, chị chốt giúp em nhé?"


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
async def user(test_db):
    return await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")


@pytest.fixture
def models(monkeypatch):
    sup = scripted([], tags=("supervisor",))
    agent = scripted([])
    phrase_model = scripted([])
    classifier = {"label": "approve", "calls": 0}

    async def fake_classify(proposal, bot_question, customer_text):
        classifier["calls"] += 1
        return classifier["label"]

    monkeypatch.setattr(supervisor, "build_chat_model", lambda **kw: sup)
    monkeypatch.setattr(agents, "build_chat_model", lambda **kw: agent)
    monkeypatch.setattr(phrase, "build_chat_model", lambda **kw: phrase_model)
    monkeypatch.setattr(rewrite, "build_chat_model", lambda **kw: scripted([AIMessage("")]))
    monkeypatch.setattr(reply, "classify_reply", fake_classify)
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    monkeypatch.setattr(events, "get_callbacks", lambda: [])
    return SimpleNamespace(sup=sup, agent=agent, phrase=phrase_model, classifier=classifier)


async def turn(test_db, user, text):
    return [(e.type, e.data) async for e in events.run_turn(test_db, user, text)]


async def propose_turn(test_db, user, models, hour=15):
    """Lượt 1: booking giữ chỗ rồi hỏi xác nhận → interrupt."""
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {"start_at": tomorrow_at(hour).isoformat(), "note": "làm tóc"}),
        AIMessage(QUESTION),
    ]
    out = await turn(test_db, user, "mai 3 giờ chiều chị làm tóc")
    assert out[-1] == ("complete", {"answer": QUESTION})
    assert ("token", {"text": QUESTION}) in out          # câu hỏi xác nhận stream như thường
    return out


async def appointments(db):
    return await db["appointments"].count_documents({"status": "booked"})


async def test_proposal_turn_ends_waiting_on_an_interrupt(test_db, user, models):
    await propose_turn(test_db, user, models)
    from app.agents.booking_graph.graph import build_graph
    config = {"configurable": {"thread_id": events.thread_id_for(str(user.id), now_utc())}}
    snap = await build_graph(test_db, user).aget_state(config)
    assert len(snap.interrupts) == 1
    assert await appointments(test_db) == 0


async def test_g01_ko_em_never_books(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ vâng, chị muốn đổi sang giờ nào ạ?")]
    agent_calls = len(models.agent.calls)

    out = await turn(test_db, user, "dạ ko em")

    assert await appointments(test_db) == 0
    assert len(models.agent.calls) == agent_calls + 1     # lượt 2 đi vào booking
    assert len(models.sup.calls) == 1                     # không qua supervisor
    assert models.classifier["calls"] == 0                # lưới chặn trước model
    assert out[-1] == ("complete", {"answer": "Dạ vâng, chị muốn đổi sang giờ nào ạ?"})


async def test_tan_gau_with_phai_never_books(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ làm móng bột tầm một tiếng chị ạ.")]

    out = await turn(test_db, user, TAN_GAU)

    assert await appointments(test_db) == 0
    assert "Xong rồi" not in out[-1][1]["answer"]
    assert models.classifier["calls"] == 0


async def test_approve_books_and_phrase_streams(test_db, user, models):
    await propose_turn(test_db, user, models)
    when = format_vi_datetime(tomorrow_at(15))
    models.phrase.replies += [AIMessage(f"Dạ xong rồi chị Lan, hẹn chị {when} nhé.")]

    out = await turn(test_db, user, "ừ em")

    booked = await AppointmentService(test_db).upcoming_for(user)
    assert len(booked) == 1 and booked[0].start_at.astimezone(TZ) == tomorrow_at(15)
    assert any(t == "token" for t, _ in out)
    assert out[-1] == ("complete", {"answer": f"Dạ xong rồi chị Lan, hẹn chị {when} nhé."})


async def test_g07_after_tool_loop_da_vang_de_chi_goi_never_books(test_db, user, models):
    """Vòng tool cạn sau khi đã giữ chỗ: câu cuối là FALLBACK, không có interrupt;
    lượt sau là tin mới, kể cả khi bộ phân loại sẽ nói approve."""
    models.sup.replies += [AIMessage("booking"), AIMessage("booking")]
    models.agent.replies += [tool_call("propose_appointment",
                                       {"start_at": tomorrow_at(15).isoformat()}, id="c0")]
    models.agent.replies += [tool_call("list_my_appointments", {}, id=f"c{i}") for i in range(1, 5)]
    first = await turn(test_db, user, "à mà giờ đó còn chỗ không em")
    assert first[-1][0] == "complete"

    models.agent.replies += [AIMessage("Dạ chị gọi xong nhắn em nhé.")]
    await turn(test_db, user, "dạ vâng để chị gọi")

    assert await appointments(test_db) == 0
    assert len(models.sup.calls) == 2                      # lượt 2 đi lại từ START
    assert models.classifier["calls"] == 0


async def test_failed_turn_leaves_no_pending_proposal(test_db, user, models):
    """Lượt 1 giữ chỗ rồi model nổ (error) → không có interrupt. Lượt 2 "ok đặt
    giúp chị" chạy từ START, không ghi. Model nổ HAI lần liên tiếp (F13)."""
    models.sup.replies += [AIMessage("booking"), AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat()}),
        RuntimeError("azure 500"), RuntimeError("azure 500"),
    ]
    first = await turn(test_db, user, "mai 3 giờ chiều chị làm tóc")
    assert first[-1][0] == "error"

    # Bỏ phần lỗi chưa dùng (chưa có retry thì lần nổ thứ hai còn đó; có retry
    # ở M4 thì đã dùng hết) — lượt 2 bắt đầu sạch dù có hay không retry.
    models.agent.replies.clear()
    models.agent.replies += [AIMessage("Dạ chị muốn đặt giờ nào ạ?")]
    second = await turn(test_db, user, "ok đặt giúp chị")

    assert second[-1][0] == "complete"
    assert len(models.sup.calls) == 2
    assert await appointments(test_db) == 0
    assert models.classifier["calls"] == 0


async def test_resume_turn_is_logged_in_conversations(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.phrase.replies += [AIMessage("")]                # phrase lỗi → câu cứng (fallback)

    out = await turn(test_db, user, "ừ")

    answer = out[-1][1]["answer"]
    assert answer.startswith("Xong rồi ạ")
    doc = await test_db["conversations"].find_one({"user_id": str(user.id)})
    pairs = [(m["role"], m["content"]) for m in doc["messages"]]
    assert pairs == [("user", "mai 3 giờ chiều chị làm tóc"), ("assistant", QUESTION),
                     ("user", "ừ"), ("assistant", answer)]
    assert doc["messages"][-1]["source"] == "code"


async def test_unclear_asks_once_then_approve_books(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.classifier["label"] = "unclear"

    second = await turn(test_db, user, "cho cô dùng thử")

    when = format_vi_datetime(tomorrow_at(15))
    assert second[-1] == ("complete", {"answer": f"Dạ chị Lan chốt giúp em lịch {when} nha, đúng không ạ?"})
    assert await appointments(test_db) == 0

    models.classifier["label"] = "approve"
    models.phrase.replies += [AIMessage("")]
    third = await turn(test_db, user, "ừ")

    assert third[-1][1]["answer"].startswith("Xong rồi ạ")
    assert await appointments(test_db) == 1


async def test_unclear_twice_drops_the_proposal_and_goes_to_booking(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.classifier["label"] = "unclear"
    await turn(test_db, user, "cho cô dùng thử")
    models.agent.replies += [AIMessage("Dạ chị cần em giúp gì thêm ạ?")]

    out = await turn(test_db, user, "ờ thì để coi")

    assert out[-1] == ("complete", {"answer": "Dạ chị cần em giúp gì thêm ạ?"})
    assert await test_db["proposals"].count_documents({"status": "rejected"}) == 1
    assert await appointments(test_db) == 0


async def test_approved_proposal_does_not_carry_over(test_db, user, models):
    """Thay test F3 cũ: đề xuất đã dùng không được dùng lại ở lượt sau."""
    await propose_turn(test_db, user, models)
    models.phrase.replies += [AIMessage("")]
    await turn(test_db, user, "ừ")
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage("Dạ chị Lan cần gì cứ nhắn em nhé.")]

    out = await turn(test_db, user, "ừ cảm ơn em")

    assert out[-1] == ("complete", {"answer": "Dạ chị Lan cần gì cứ nhắn em nhé."})
    assert len(models.sup.calls) == 2                       # về supervisor
    assert await appointments(test_db) == 1


async def test_expired_proposal_is_a_new_message(test_db, user, models, monkeypatch):
    await propose_turn(test_db, user, models)
    future = now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES + 1)
    monkeypatch.setattr(proposal_module, "now_utc", lambda: future)
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chị muốn đặt lại giờ nào ạ?")]

    out = await turn(test_db, user, "ừ")

    assert out[-1] == ("complete", {"answer": "Dạ chị muốn đặt lại giờ nào ạ?"})
    assert len(models.sup.calls) == 2
    assert await appointments(test_db) == 0


async def test_missing_checkpoint_never_approves_a_stale_proposal(test_db, user, models):
    """Thread bị xoá (TTL) mà đề xuất vẫn pending trong DB: không có interrupt →
    lượt mới từ START, không có đường nào tự lấy đề xuất ra mà duyệt."""
    await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage("Dạ chị cần gì ạ?")]

    await turn(test_db, user, "ừ")

    assert await appointments(test_db) == 0
    assert models.classifier["calls"] == 0


async def test_new_turn_while_waiting_sees_the_question_in_history(test_db, user, models):
    """Câu hỏi xác nhận nằm trong thread (record trước confirm): agent ở lượt
    resume thấy nó trong lịch sử như mọi câu đáp khác."""
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ vâng ạ.")]
    await turn(test_db, user, "thôi khỏi em")
    batch = [m.content for m in models.agent.calls[-1]]
    assert QUESTION in batch and batch[-1] == "thôi khỏi em"


async def test_resume_on_the_real_mongo_saver(test_db, user, models, monkeypatch):
    """Interrupt + resume qua serializer của MongoDBSaver thật, trong DB test."""
    from pymongo import MongoClient

    from app.core.config import settings
    from tests.conftest import TEST_DB_NAME, TEST_MONGO_URI
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    client = MongoClient(TEST_MONGO_URI, tz_aware=True)
    checkpoint.set_checkpointer(checkpoint.make_checkpointer(client))
    try:
        await propose_turn(test_db, user, models)
        models.phrase.replies += [AIMessage("")]
        out = await turn(test_db, user, "ừ")
        assert out[-1][1]["answer"].startswith("Xong rồi ạ")
        assert await appointments(test_db) == 1
    finally:
        client.close()
