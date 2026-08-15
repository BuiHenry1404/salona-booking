from datetime import datetime, timedelta

import pytest
from langchain_core.messages import HumanMessage

from app.agents.booking_graph.confirm import is_affirmative, make_confirm_node
from app.core.clock import TZ
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation import ConversationService

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("text", ["ừ", "Ừ", "đúng rồi", "ok", "OK ạ", "vâng", "dạ đúng", "được"])
def test_affirmative_phrases(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["không", "thôi khỏi", "đổi giờ khác", "để mai đi", "chưa"])
def test_negative_phrases(text):
    assert is_affirmative(text) is False


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def _setup(db):
    user = await AuthService(db).create_user("0912345678", "matkhau123", "Cô Lan")
    pending = {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}
    await ConversationService(db).set_pending(str(user.id), pending)
    return user


def a_state(user, text, pending):
    return {"messages": [HumanMessage(content=text)], "user_id": str(user.id),
            "context_block": "", "pending_confirmation": pending}


async def test_propose_then_yes_books_the_time_from_MONGO(test_db):
    """Test nối hai đầu: `propose_appointment` ghi cờ, `confirm` đọc lại.

    Điểm quan trọng: giá trị đem đi ghi lịch lấy từ Mongo, KHÔNG phải từ chuỗi
    model gõ lại ở lượt sau. Model chép sai 15:00 thành 5:00 cũng không ảnh
    hưởng, vì nó không còn được chạm vào con số đó nữa.
    """
    from app.agents.booking_graph.tools import make_booking_tools

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    start = tomorrow_at(15)

    tools = make_booking_tools(test_db, user)
    propose = next(t for t in tools if t.name == "propose_appointment")
    await propose.ainvoke({"start_at": start.isoformat(), "note": "làm tóc"})

    pending = await ConversationService(test_db).get_pending(str(user.id))
    result = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

    assert "xong" in result["answer"].lower()
    booked = await AppointmentService(test_db).upcoming_for(user)
    assert len(booked) == 1
    assert booked[0].start_at.astimezone(TZ) == start
    assert booked[0].note == "làm tóc"


async def test_yes_creates_the_appointment(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    node = make_confirm_node(test_db, user)
    result = await node(a_state(user, "ừ", pending))

    assert "xong" in result["answer"].lower()
    assert len(await AppointmentService(test_db).upcoming_for(user)) == 1


async def test_yes_clears_the_pending_flag(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    await make_confirm_node(test_db, user)(a_state(user, "đúng rồi", pending))
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_no_clears_the_flag_without_booking(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    result = await make_confirm_node(test_db, user)(a_state(user, "thôi khỏi", pending))

    assert await AppointmentService(test_db).upcoming_for(user) == []
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
    assert result["answer"]


async def test_taken_slot_produces_a_friendly_message_not_a_crash(test_db):
    user = await _setup(test_db)
    other = await AuthService(test_db).create_user("0938111222", "x", "Cô Hoa")
    await AppointmentService(test_db).create(other, tomorrow_at(15), note=None)

    pending = await ConversationService(test_db).get_pending(str(user.id))
    result = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

    assert "có người" in result["answer"].lower()
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
