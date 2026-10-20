"""Đầu-cuối qua `run_turn` (M3): lượt 1 đề xuất + hỏi xác nhận → interrupt; lượt 2
resume bằng câu khách → code quyết.

Graph thật, model giả cho supervisor/agent/phrase, bộ phân loại giả (thường là
LUÔN approve — để chứng minh lưới code tự chặn được các câu đã làm A ghi sai).
DB `chatbot_test_db`; checkpointer `InMemorySaver` (conftest), trừ một test chạy
trên `MongoDBSaver` thật.
"""
import json
from datetime import datetime, timedelta, timezone
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
    # Lượt 2 đi lại từ START (không qua confirm: bộ phân loại không chạy). Câu
    # hoãn được code định tuyến vào booking (lỗi live B3 #2) — model supervisor
    # chỉ chạy ở lượt 1.
    assert len(models.sup.calls) == 1
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


# --- fix round 1 (review M3) --------------------------------------------------

async def test_cancelled_resume_mid_approve_then_yes_gives_the_confirmation(
        test_db, user, models, monkeypatch):
    """Lượt "ừ" bị huỷ (lượt mới thay) đúng lúc đang ghi lịch. Lịch vẫn ghi xong
    (shield), interrupt vẫn treo; lượt "ừ ạ" sau đó phải nhận câu chốt — đúng
    một lịch, đề xuất `approved` — chứ không bị coi là tin mới."""
    import asyncio

    from app.services import appointment as appointment_module
    await propose_turn(test_db, user, models)

    async def slow_notify(appt):
        await asyncio.sleep(0.6)
    monkeypatch.setattr(appointment_module.notifications, "appointment_created", slow_notify)

    second = asyncio.create_task(turn(test_db, user, "ừ"))
    await asyncio.sleep(0.2)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second

    models.phrase.replies.clear()
    models.phrase.replies += [AIMessage("")]                 # câu chốt = câu cứng
    third = await turn(test_db, user, "ừ ạ")

    assert third[-1][0] == "complete"
    assert third[-1][1]["answer"].startswith("Xong rồi ạ")
    assert await appointments(test_db) == 1
    assert (await test_db["proposals"].find_one({}))["status"] == "approved"
    assert models.classifier["calls"] <= 1                    # chỉ lượt bị huỷ (nếu kịp)


async def test_resume_across_midnight_keeps_the_pending_interrupt(test_db, user, models, monkeypatch):
    """Hỏi xác nhận lúc 23:59, khách "ừ" lúc 00:01: `pick_thread` nối thread hôm
    qua (luật 30 phút), thấy interrupt đang chờ → resume → ghi đúng một lịch."""
    from datetime import time as dtime

    from app.core.clock import now_ms
    real_now = now_ms()
    local = real_now.astimezone(TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), dtime.min, tzinfo=TZ)
    skew = {"delta": midnight - timedelta(seconds=60) - real_now}       # → 23:59:00

    monkeypatch.setattr(events, "now_ms", lambda: now_ms() + skew["delta"])
    real_last = events._last_checkpoint_at
    first_delta = skew["delta"]

    async def skewed_last(graph, thread_id):
        at = await real_last(graph, thread_id)
        return at + first_delta if at else None
    monkeypatch.setattr(events, "_last_checkpoint_at", skewed_last)

    await propose_turn(test_db, user, models)
    yesterday_thread = events.thread_id_for(str(user.id), midnight - timedelta(seconds=60))

    skew["delta"] = first_delta + timedelta(seconds=120)                 # → 00:01:00
    models.phrase.replies += [AIMessage("")]
    out = await turn(test_db, user, "ừ")

    assert out[-1][1]["answer"].startswith("Xong rồi ạ")
    assert await appointments(test_db) == 1
    assert len(models.sup.calls) == 1                                    # resume, không về START
    from app.agents.booking_graph.graph import build_graph
    snap = await build_graph(test_db, user).aget_state(
        {"configurable": {"thread_id": yesterday_thread}})
    assert snap.values["messages"][-2].content == "ừ"


async def test_resume_cancelled_while_classifying_then_yes_books_once(test_db, user, models, monkeypatch):
    """Lượt "ừ" bị huỷ khi bộ phân loại còn đang chạy (chưa ghi gì). Lần resume
    sau không được nổ InvalidUpdateError vì update của lượt bị huỷ còn treo, và
    phải ghi đúng một lịch."""
    import asyncio
    await propose_turn(test_db, user, models)
    calls = {"n": 0}

    async def slow_then_approve(proposal, bot_question, customer_text):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(5)
        return "approve"
    monkeypatch.setattr(reply, "classify_reply", slow_then_approve)

    second = asyncio.create_task(turn(test_db, user, "ừ"))
    await asyncio.sleep(0.2)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert await appointments(test_db) == 0

    models.phrase.replies += [AIMessage("")]
    third = await turn(test_db, user, "ừ đúng rồi em")

    assert third[-1][0] == "complete", third[-1]
    assert third[-1][1]["answer"].startswith("Xong rồi ạ")
    assert await appointments(test_db) == 1


# --- fix round 2 ---------------------------------------------------------------

async def test_new_message_after_cut_before_confirm_without_proposal(test_db, user, models):
    """Lượt bị huỷ giữa `record` và `confirm` khi KHÔNG có đề xuất: checkpoint có
    next=confirm nhưng `proposal_id` rỗng. Tin sau là tin MỚI (supervisor), không
    được resume rồi trả `complete` rỗng và mất khỏi `conversations` (review M3)."""
    from app.agents.booking_graph.graph import build_graph
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage("Dạ chào chị ạ")]
    assert (await turn(test_db, user, "chào em"))[-1][1]["answer"] == "Dạ chào chị ạ"

    graph = build_graph(test_db, user)
    config = {"configurable": {
        "thread_id": await events.pick_thread(graph, str(user.id), events.now_ms())}}
    await graph.aupdate_state(config, {}, as_node="record")          # cắt sau record
    snap = await graph.aget_state(config)
    assert "confirm" in snap.next and not snap.interrupts and not snap.values.get("proposal_id")

    models.sup.replies += [AIMessage("shop")]
    models.agent.replies += [AIMessage("Dạ sơn gel 150 nghìn ạ")]
    out = await turn(test_db, user, "sơn gel bao nhiêu em")

    assert out[-1] == ("complete", {"answer": "Dạ sơn gel 150 nghìn ạ"})
    assert len(models.sup.calls) == 2
    doc = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert doc["messages"][-1]["content"] == "Dạ sơn gel 150 nghìn ạ"


async def test_proposal_set_but_not_yet_interrupted_treats_next_message_as_the_reply(
        test_db, user, models):
    """Lượt đề xuất bị huỷ sau `record`, trước khi confirm interrupt: tin sau là
    câu TRẢ LỜI đề xuất — lưới + bộ phân loại vẫn gác việc ghi ("dạ ko em" → không ghi)."""
    from app.agents.booking_graph.graph import build_graph
    from app.services.proposal import ProposalService
    p = await ProposalService(test_db).create(user, "book", tomorrow_at(15), None)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage(QUESTION)]
    await turn(test_db, user, "mai 3 giờ chiều chị làm tóc")
    graph = build_graph(test_db, user)
    config = {"configurable": {
        "thread_id": await events.pick_thread(graph, str(user.id), events.now_ms())}}
    await graph.aupdate_state(config, {"proposal_id": str(p.id)}, as_node="record")

    models.agent.replies += [AIMessage("Dạ vâng, chị muốn giờ nào ạ?")]
    out = await turn(test_db, user, "dạ ko em")

    assert out[-1] == ("complete", {"answer": "Dạ vâng, chị muốn giờ nào ạ?"})
    assert await appointments(test_db) == 0
    assert len(models.sup.calls) == 1                   # trả lời đề xuất, không về START


# --- lỗi live 3/5/6: sau khi khách KHÔNG chốt, agent phải biết ---------------

def context_seen(models, call=-1) -> str:
    """Khối bối cảnh (HumanMessage "Bây giờ là…") trong lần gọi model agent."""
    batch = models.agent.calls[call]
    return next(str(m.content) for m in batch if str(m.content).startswith("Bây giờ là"))


async def test_decline_tells_the_agent_what_was_declined(test_db, user, models):
    """Lỗi live 3: "dạ ko em" → agent không biết đề xuất vừa bị từ chối nên
    đề xuất lại ngay. Giờ khối bối cảnh của lượt đó nói rõ, từ DB."""
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ vâng, chị muốn đổi sang giờ nào ạ?")]

    await turn(test_db, user, "dạ ko em")

    ctx = context_seen(models)
    assert "KHÔNG chốt" in ctx
    assert format_vi_datetime(tomorrow_at(15)) in ctx
    assert f"[declined: book, iso: {tomorrow_at(15).isoformat()}]" in ctx
    assert "chưa ghi gì" in ctx
    assert ctx.count("[declined:") == 1
    # Dòng nằm TRƯỚC dòng chốt "hãy tin phần trên".
    assert ctx.rstrip().endswith("hãy tin phần trên.")


async def test_decline_line_survives_one_more_turn_then_drops(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ vâng ạ.")]
    await turn(test_db, user, "dạ ko em")

    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chưa đặt gì ạ.")]
    await turn(test_db, user, "vậy giờ sao em")
    assert "[declined: book" in context_seen(models)          # lượt kế: còn

    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chị cần gì ạ?")]
    await turn(test_db, user, "à mà thôi")
    assert "[declined: book" not in context_seen(models)      # thêm một lượt: hết


async def test_change_decision_is_handed_over_too(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.classifier["label"] = "change"
    models.agent.replies += [AIMessage("Dạ chị muốn đổi sang giờ nào ạ?")]
    await turn(test_db, user, "để coi")
    assert "[declined: book" in context_seen(models)
    assert await test_db["proposals"].count_documents({"status": "superseded"}) == 1


async def test_unclear_twice_hands_the_drop_over(test_db, user, models):
    await propose_turn(test_db, user, models)
    models.classifier["label"] = "unclear"
    await turn(test_db, user, "cho cô dùng thử")
    models.agent.replies += [AIMessage("Dạ chị cần em giúp gì thêm ạ?")]
    await turn(test_db, user, "ờ thì để coi")
    assert "[declined: book" in context_seen(models)


async def test_cancel_deferral_then_status_question_knows_the_cancel_is_off(test_db, user, models):
    """Lỗi live 6: "để cô hỏi thằng Huy đã" (hoãn) rồi "lịch cô còn hay mất
    rồi em?" — lượt sau phải thấy: chưa hủy, lịch vẫn còn."""
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), note="làm tóc")
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [tool_call("cancel_appointment", {"appointment_id": str(appt.id)}),
                             AIMessage("Dạ chị muốn hủy lịch mai 3 giờ chiều đúng không ạ?")]
    await turn(test_db, user, "hủy giùm cô cái lịch đó đi em")

    models.agent.replies += [AIMessage("Dạ vâng ạ.")]
    await turn(test_db, user, "để cô hỏi thằng Huy đã")
    assert "chưa hủy, lịch vẫn còn" in context_seen(models)

    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ lịch vẫn còn ạ.")]
    await turn(test_db, user, "ủa vậy giờ lịch cô còn hay mất rồi em?")
    ctx = context_seen(models)
    assert "chưa hủy, lịch vẫn còn" in ctx
    assert "[declined: cancel" in ctx
    assert await appointments(test_db) == 1


async def test_expired_proposal_is_handed_over_as_expired(test_db, user, models, monkeypatch):
    await propose_turn(test_db, user, models)
    future = now_utc() + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES + 1)
    monkeypatch.setattr(proposal_module, "now_utc", lambda: future)
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chị muốn đặt lại giờ nào ạ?")]

    await turn(test_db, user, "ừ")

    ctx = context_seen(models)
    assert "HẾT HẠN" in ctx and "[declined: book" in ctx
    assert await appointments(test_db) == 0


async def test_live_b2_claim_while_only_holding_is_rewritten_end_to_end(test_db, user, models):
    """Lỗi live B2 #4 qua graph thật: GuardMiddleware thấy `proposal_id` của lượt."""
    when = format_vi_datetime(tomorrow_at(15))
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}),
        AIMessage(f"Dạ, em đã đặt lịch cho chị Lan vào {when} rồi ạ, chị xác nhận giúp em nhé?"),
    ]
    out = await turn(test_db, user, "mai 3 giờ chiều chị làm tóc")
    assert out[-1] == ("complete", {"answer":
                       f"Dạ, em giữ chỗ cho chị Lan vào {when} rồi ạ, chị xác nhận giúp em nhé?"})
    assert await appointments(test_db) == 0


async def test_live_b2_deferral_naming_a_relative_goes_to_booking_with_the_carve_out(
        test_db, user, models):
    """Lỗi live B2 #5: "để cô hỏi thằng Huy đã" → câu từ chối quyền riêng tư
    (BOOKING_PROMPT rule 4). Lượt hoãn đi vào agent BOOKING (không supervisor,
    không ghi), và system prompt nó đọc có vế loại trừ: nhắc người thân khi hoãn
    không phải hỏi lịch người khác."""
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), note="làm tóc")
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [tool_call("cancel_appointment", {"appointment_id": str(appt.id)}),
                             AIMessage("Dạ chị muốn hủy lịch mai 3 giờ chiều đúng không ạ?")]
    await turn(test_db, user, "hủy giùm cô cái lịch đó đi em")

    models.agent.replies += [AIMessage("Dạ, chị hỏi rồi nhắn em nhé.")]
    await turn(test_db, user, "để cô hỏi thằng Huy đã")

    assert len(models.sup.calls) == 1 and models.classifier["calls"] == 0
    system = models.agent.calls[-1][0].content
    assert "will ask someone before deciding" in system
    assert await appointments(test_db) == 1


# Gọi Azure THẬT — chạy tay: `pytest tests/test_confirm_flow.py -m llm -v`.
@pytest.mark.llm
@pytest.mark.parametrize("deferral", ["để cô hỏi thằng Huy đã", "de chu hoi lai no"])
async def test_deferral_naming_a_relative_gets_no_privacy_refusal_llm(test_db, user, deferral):
    from langchain_core.messages import HumanMessage
    from app.agents.booking_graph.agents import build_agent
    from app.agents.booking_graph.context import declined_line, load_context, with_line
    from app.agents.booking_graph.graph import booking_tools
    from app.agents.booking_graph.prompts import BOOKING_PROMPT

    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), note="làm tóc")
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "cancel", appt.start_at, None, target=appt)
    await proposals.reject(user, str(proposal.id))
    context = await load_context(test_db, user, deferral)
    block = with_line(context["context_block"], declined_line(proposal))
    agent = build_agent(BOOKING_PROMPT, booking_tools(test_db, user), user=user)
    out = await agent.ainvoke({"messages": [
        HumanMessage("hủy giùm cô cái lịch đó đi em"),
        AIMessage(f"Dạ em sẽ hủy lịch {format_vi_datetime(appt.start_at)}, chị xác nhận giúp em nhé?"),
        HumanMessage(deferral)], "context_block": block})
    answer = out["answer"].lower()
    assert "chỉ xem" not in answer and "của chị lan thôi" not in answer, answer
    assert not any(getattr(m, "tool_calls", None) for m in out["messages"]), "không gọi tool"
    assert await test_db["proposals"].count_documents({"status": "pending"}) == 0


# --- Lỗi live B2 #2: chú Hùng, hai lịch trong một câu → đặt LẦN LƯỢT ----------
def in_two_days(hour):
    local = datetime.now(TZ) + timedelta(days=2)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
async def hung(test_db):
    return await AuthService(test_db).create_user("0987654321", "matkhau123", "Chú Hùng")


async def graph_values(test_db, user):
    from app.agents.booking_graph.graph import build_graph
    config = {"configurable": {"thread_id": events.thread_id_for(str(user.id), now_utc())}}
    return (await build_graph(test_db, user).aget_state(config)).values


async def test_live_b2_two_bookings_in_one_message_are_booked_one_at_a_time(test_db, hung, models,
                                                                             monkeypatch):
    nine, two = in_two_days(9), in_two_days(14)
    when9, when2 = format_vi_datetime(nine), format_vi_datetime(two)

    # Lượt 1: đề xuất CHỈ lịch đầu, lịch sau vào hàng chờ với ghi chú riêng.
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {
            "start_at": nine.isoformat(), "note": "cắt tóc cho chú",
            "queued": [{"start_at": two.isoformat(), "note": "cắt tóc cho thằng cháu"}]}),
        AIMessage(f"Dạ em đặt lần lượt từng lịch nhé. Em giữ chỗ {when9} cắt tóc cho anh Hùng, "
                  "anh xác nhận giúp em nhé?"),
    ]
    await turn(test_db, hung, "chao em, thu 4 nay chu muon cat toc luc 9h sang voi 2h chieu "
                              "cho thang chau nua")
    seen = json.loads(models.agent.calls[-1][-1].content)       # kết quả tool model đọc
    assert seen["held"]["iso"] == nine.isoformat()
    assert [q["at"]["iso"] for q in seen["queued"]] == [two.isoformat()]
    first = await pending(test_db, hung)
    assert first["note"] == "cắt tóc cho chú"
    assert [q["note"] for q in (await graph_values(test_db, hung))["queued"]] == ["cắt tóc cho thằng cháu"]

    # Lượt 2: đồng ý 9 giờ, hỏi luôn lịch 2 giờ → KHÔNG bị phủ quyết; ghi 9 giờ,
    # rồi code mời lịch đang chờ.
    seen_by_classifier = []

    async def classify(proposal, bot_question, customer_text):
        seen_by_classifier.append(proposal)
        return "approve"
    monkeypatch.setattr(reply, "classify_reply", classify)
    models.phrase.replies += [AIMessage(f"Dạ xong rồi anh Hùng, hẹn anh {when9} nhé.")]
    out = await turn(test_db, hung, "dc em, 9h la cua chu. con 2h chieu cho thang chau thi sao")
    assert seen_by_classifier and seen_by_classifier[0]["queued"] == [two.isoformat()]
    booked = await AppointmentService(test_db).upcoming_for(hung)
    assert [(b.start_at.astimezone(TZ), b.note) for b in booked] == [(nine, "cắt tóc cho chú")]
    answer = out[-1][1]["answer"]
    assert answer.startswith(f"Dạ xong rồi anh Hùng, hẹn anh {when9} nhé.")
    assert when2 in answer and "cắt tóc cho thằng cháu" in answer          # mời lịch kế

    # Lượt 3: tin mới — agent thấy dòng hàng chờ, giữ chỗ 2 giờ với ghi chú cháu.
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {"start_at": two.isoformat(),
                                          "note": "cắt tóc cho thằng cháu"}),
        AIMessage(f"Dạ 9 giờ của anh xong rồi ạ. Em giữ tiếp {when2} cắt tóc cho thằng cháu, "
                  "anh xác nhận giúp em nhé?"),
    ]
    await turn(test_db, hung, "u, roi 9h sang cua chu dau em?")
    ctx = context_seen(models, call=-2)
    assert "[queued" in ctx and when2 in ctx and "cắt tóc cho thằng cháu" in ctx
    second = await pending(test_db, hung)
    assert second["note"] == "cắt tóc cho thằng cháu"
    assert second["start_at"].replace(tzinfo=timezone.utc).astimezone(TZ) == two
    assert (await graph_values(test_db, hung)).get("queued") == []

    statuses = [(p["start_at"].replace(tzinfo=timezone.utc).astimezone(TZ).hour, p["status"])
                async for p in test_db["proposals"].find({"user_id": str(hung.id)}).sort("created_at", 1)]
    assert statuses == [(9, "approved"), (14, "pending")]       # không huỷ qua lại


async def pending(db, user):
    docs = await db["proposals"].find({"user_id": str(user.id), "status": "pending"}).to_list(10)
    assert len(docs) == 1, docs
    return docs[0]


async def test_queue_line_disappears_once_nothing_waits(test_db, user, models):
    """Không có hàng chờ thì không có dòng [queued …] — đường cũ không đổi."""
    await propose_turn(test_db, user, models)
    models.agent.replies += [AIMessage("Dạ vâng, chị muốn đổi sang giờ nào ạ?")]
    await turn(test_db, user, "dạ ko em")
    assert "[queued" not in context_seen(models)


# --- Lỗi live B3 (chú Hùng, history-B3) ---------------------------------------
async def hung_first_turn(test_db, hung, models, agent_reply=None):
    nine, two = in_two_days(9), in_two_days(14)
    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [
        tool_call("propose_appointment", {
            "start_at": nine.isoformat(), "note": "cắt tóc cho anh Hùng",
            "queued": [{"start_at": two.isoformat(), "note": "cắt tóc cho cháu"}]}),
        AIMessage(agent_reply or f"Em đang giữ lịch cắt tóc cho anh Hùng vào "
                                 f"{format_vi_datetime(nine)}. Anh Hùng xác nhận giúp em nhé?"),
    ]
    return await turn(test_db, hung, "chao em, thu 4 nay chu muon cat toc luc 9h sang voi 2h "
                                     "chieu cho thang chau nua")


async def test_live_b3_confirmation_never_claims_the_queued_booking(test_db, hung, models):
    """Lượt 2: câu chốt LLM bịa "2 giờ chiều em cũng đã giữ cho cháu rồi" trong
    khi 2 giờ chỉ đang chờ. Phrase được báo là CHƯA giữ, và guard đổi sang câu
    cứng khi câu chốt vẫn nhận vơ."""
    nine, two = in_two_days(9), in_two_days(14)
    when9, when2 = format_vi_datetime(nine), format_vi_datetime(two)
    await hung_first_turn(test_db, hung, models)
    models.phrase.replies += [AIMessage(
        f"Dạ em đã ghi cho anh Hùng lúc {when9}. Còn 2 giờ chiều em cũng đã giữ cho cháu rồi ạ.")]
    out = await turn(test_db, hung, "dc em, 9h la cua chu. con 2h chieu cho thang chau thi sao")
    answer = out[-1][1]["answer"]
    assert "đã giữ cho cháu" not in answer
    assert answer.startswith(f"Xong rồi ạ. Hẹn gặp anh Hùng {when9} nhé.")
    assert when2 in answer                                         # câu mời lịch kế vẫn còn
    system = models.phrase.calls[-1][0].content
    assert when2 in system and "NOT held" in system


async def test_live_b3_asking_if_a_relative_is_free_reaches_booking_with_the_aside(
        test_db, hung, models):
    """Lượt 4–5: "nó có rảnh không" là hỏi đứa cháu, không phải chủ tiệm; và câu
    hoãn nhắc người nhà không phải chuyện quyền riêng tư. Agent đọc dòng code
    viết [relative]/[deferral]; lượt 5 không gọi supervisor model."""
    await hung_first_turn(test_db, hung, models)
    models.agent.replies += [AIMessage("Dạ, chú hỏi cháu rồi nhắn em nhé.")]
    await turn(test_db, hung, "khoan da, de chu hoi lai no co ranh khong")
    ctx = context_seen(models)
    assert "[relative]" in ctx and "[deferral]" in ctx
    assert await test_db["proposals"].count_documents({"status": "pending"}) == 0

    sup_calls = len(models.sup.calls)
    models.agent.replies += [AIMessage("Dạ vâng, anh Hùng hỏi xong nhắn em nhé.")]
    await turn(test_db, hung, "ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi "
                              "nhan lai em sau nha")
    assert len(models.sup.calls) == sup_calls
    assert "[relative]" in context_seen(models)
    assert await appointments(test_db) == 0


# Gọi Azure THẬT — chạy tay: `pytest tests/test_confirm_flow.py -m llm -v`.
@pytest.mark.llm
@pytest.mark.parametrize("text", [
    "khoan da, de chu hoi lai no co ranh khong",
    "khoan đã, để chú hỏi lại nó có rảnh không",
    "ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi nhan lai em sau nha",
    "không, chú hỏi thằng cháu có rảnh không ạ. thôi để chú hỏi nó rồi nhắn lại em sau nha",
])
async def test_relative_free_question_gets_a_receptionist_reply_llm(test_db, hung, text):
    from langchain_core.messages import HumanMessage
    from app.agents.booking_graph.agents import build_agent
    from app.agents.booking_graph.context import load_context, with_line
    from app.agents.booking_graph.graph import booking_tools
    from app.agents.booking_graph.prompts import BOOKING_PROMPT
    from app.agents.booking_graph.relatives import aside_line

    context = await load_context(test_db, hung, text)
    agent = build_agent(BOOKING_PROMPT, booking_tools(test_db, hung), user=hung)
    out = await agent.ainvoke({"messages": [
        HumanMessage("thu 4 nay chu muon cat toc luc 2h chieu cho thang chau"),
        AIMessage("Dạ em giữ 2 giờ chiều Thứ Tư cho cháu, anh Hùng xác nhận giúp em nhé?"),
        HumanMessage(text)],
        "context_block": with_line(context["context_block"], aside_line(text)),
        "customer_text": text})
    answer = out["answer"].lower()
    assert "chỉ xem" not in answer and "chủ tiệm" not in answer, answer
    called = [c["name"] for m in out["messages"] for c in (getattr(m, "tool_calls", None) or [])]
    assert "get_shop_status" not in called and "get_shop_hours" not in called, called
