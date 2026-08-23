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


class TestAffirmativeVariants:
    """Bảng từ đồng ý ở docs/test-scenarios/02-llm-chat-scenarios.md (LLM-05)
    và biến thể không dấu.

    Khách lớn tuổi gõ điện thoại phần lớn không bỏ dấu. Nhận nhầm "u dung roi"
    thành từ chối là bắt cụ già gõ lại — mà gõ lại thì vẫn không dấu.
    """

    @pytest.mark.parametrize("text", ["ờ", "uhm", "chính xác"])
    def test_words_the_project_spec_promises(self, text):
        """Ba từ này nằm trong bảng LLM-05 mà code chưa nhận."""
        assert is_affirmative(text) is True

    @pytest.mark.parametrize(
        "text",
        ["u", "u con", "ua", "dung roi", "da dung", "chuan roi", "chinh xac",
         "vang", "vang a", "dong y", "duoc do", "duoc nha con"],
    )
    def test_no_diacritics_still_counts_as_yes(self, text):
        assert is_affirmative(text) is True

    @pytest.mark.parametrize(
        "text",
        ["khong", "thoi khoi", "doi gio khac", "chua", "de mai di"],
    )
    def test_no_diacritics_negatives_still_win(self, text):
        """Phủ định vẫn phải thắng khẳng định khi bỏ dấu — sai hướng này là
        đặt nhầm lịch cho khách."""
        assert is_affirmative(text) is False

    def test_a_negative_inside_an_affirmative_still_loses(self):
        assert is_affirmative("dung roi nhung doi gio khac") is False

    def test_unrelated_words_containing_yes_letters_are_not_yes(self):
        """"dùng" không phải "đúng", "cua" không phải "ừa" — bỏ dấu xong dễ đụng."""
        assert is_affirmative("cho cô dùng thử") is False


class TestConfirmUsesTheRightHonorific:
    """Câu chốt lịch phải gọi khách đúng như đã gọi lúc hỏi xác nhận.

    Node confirm chạy 0 lượt LLM nên không tự suy ra được danh xưng: "Nguyễn
    Thị Lan" không cho biết khách là cô, bác hay chú. Vì vậy danh xưng do model
    điền lúc propose_appointment và lưu kèm pending — confirm chỉ đọc lại.

    Gọi "cô chú" cho một người khách vừa tự xưng "bác" nghe như đọc loa phường.
    """

    async def test_the_honorific_from_the_proposal_is_used(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Bùi Văn Ba")
        await ConversationService(test_db).set_pending(str(user.id), {
            "start_at": tomorrow_at(15).isoformat(),
            "note": "cắt tóc",
            "xung_ho": "bác Ba",
        })
        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="ừ")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(),
                              "note": "cắt tóc", "xung_ho": "bác Ba"}})

        assert "bác Ba" in out["answer"]
        assert "cô chú" not in out["answer"]

    async def test_without_an_honorific_it_does_not_invent_one(self, test_db):
        """Thiếu danh xưng thì bỏ hẳn lời xưng hô, KHÔNG rơi về "cô chú" —
        đoán sai còn tệ hơn không gọi."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="ừ")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(), "note": None}})

        assert "cô chú" not in out["answer"]
        assert "Xong rồi ạ" in out["answer"]

    async def test_the_booking_time_is_still_in_the_sentence(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Bùi Văn Ba")
        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="ừ")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(),
                              "note": None, "xung_ho": "bác Ba"}})

        assert "3 giờ chiều" in out["answer"]
