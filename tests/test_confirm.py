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
    # Cờ pending phải được xoá TRƯỚC khi chuyển tiếp, không thì lượt sau lại
    # rơi vào confirm thay vì booking và khách kẹt trong vòng lặp.
    assert result == {"route": "booking"}


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

    Node confirm chạy 0 lượt LLM, nhưng KHÔNG cần model mách nữa: danh xưng suy
    ra bằng code từ `full_name`, vốn mang sẵn tiền tố ("Chú Ba", "Cô Lan"). Nhận
    từ model là mong manh — model quên truyền thì câu chốt mất lời gọi.

    Tiền tố được MAP chứ không chép lại: "Chú Ba" thành "anh Ba". Prompt cấm
    tuyệt đối nói "cô", "chú", "bác" — chúng không đi cùng giọng "em — anh/chị".
    """

    async def test_the_honorific_is_derived_from_the_customer_name(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Chú Ba")
        await ConversationService(test_db).set_pending(str(user.id), {
            "start_at": tomorrow_at(15).isoformat(),
            "note": "cắt tóc",
        })
        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="ừ")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(),
                              "note": "cắt tóc"}})

        assert "anh Ba" in out["answer"]
        # "Chú" là tiền tố để SUY giới tính, không phải chữ để nói lại với khách.
        assert "chú" not in out["answer"].lower()
        assert "cô chú" not in out["answer"]

    async def test_without_a_derivable_honorific_it_does_not_invent_one(self, test_db):
        """Tên không có tiền tố thì lùi về "anh chị", KHÔNG rơi về "cô chú" —
        đoán sai còn tệ hơn không gọi."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Nguyễn Thị Lan")
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
                              "note": None}})

        assert "3 giờ chiều" in out["answer"]


class TestHonorificInTheOtherBranches:
    """Danh xưng phải dùng ở MỌI nhánh của confirm, không riêng nhánh thành công.

    Nhánh ghi hụt (giờ vừa bị người khác đặt mất) vẫn phải gọi khách đúng —
    gọi họ là "cô chú" ở đó thì công sức xưng hô ở trên đổ sông đổ bể.
    """

    async def test_a_taken_slot_keeps_the_honorific(self, test_db):
        """Giờ vừa bị người khác đặt mất giữa hai lượt."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Chú Ba")
        other = await AuthService(test_db).create_user("0999888777", "matkhau123", "Người khác")
        await AppointmentService(test_db).create(other, tomorrow_at(15), "làm tóc")

        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="ừ")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(),
                              "note": None}})

        assert "cô chú" not in out["answer"].lower(), out["answer"]

    async def test_the_decline_branch_hands_over_instead_of_answering(self, test_db):
        """Nhánh chưa-đồng-ý không còn câu nào để xưng hô: nó chuyển tiếp sang
        booking, nơi LLM tự viết câu bằng lịch sử và khối bối cảnh."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Chú Ba")
        node = make_confirm_node(test_db, user)
        out = await node({"messages": [HumanMessage(content="thôi khỏi")],
                          "pending_confirmation": {
                              "start_at": tomorrow_at(15).isoformat(), "note": None}})

        assert out == {"route": "booking"}


class TestNotAffirmativeGoesToBooking:
    """Khách nói "khoan để chị xem lại" thì vẫn đang đặt lịch — đẩy sang
    booking để nó trả lời bằng lịch sử và đủ tool, thay vì đọc một câu cứng
    giục khách chọn giờ."""

    async def test_hesitation_does_not_write_an_appointment(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        node = make_confirm_node(test_db, user)
        state = {
            "messages": [HumanMessage(content="à khoan, để chị xem lại")],
            "user_id": str(user.id),
            "pending_confirmation": {"start_at": "2026-09-01T02:00:00+00:00", "note": "làm tóc"},
        }

        result = await node(state)

        assert result.get("route") == "booking"
        assert "answer" not in result
        assert await test_db["appointments"].count_documents({}) == 0

    async def test_affirmative_still_writes_and_answers_without_llm(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        node = make_confirm_node(test_db, user)
        start = (datetime.now(TZ) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        state = {
            "messages": [HumanMessage(content="ừ chốt luôn nha")],
            "user_id": str(user.id),
            "pending_confirmation": {"start_at": start.isoformat(), "note": "làm tóc"},
        }

        result = await node(state)

        assert "Xong rồi ạ" in result["answer"]
        assert "chị Lan" in result["answer"]
        assert result.get("route") is None
        assert await test_db["appointments"].count_documents({}) == 1


class TestConfirmReschedules:
    """BUG-1: chốt "ừ" sau khi giữ chỗ giờ mới CÓ `replaces_appointment_id`
    phải dời lịch — không đặt thêm."""

    async def test_yes_moves_instead_of_adding(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Chú Hùng")
        old = await AppointmentService(test_db).create(user, tomorrow_at(9), "làm tóc")
        pending = {"start_at": tomorrow_at(10).isoformat(), "note": None,
                   "replaces_appointment_id": str(old.id)}

        out = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

        upcoming = await AppointmentService(test_db).upcoming_for(user)
        assert [a.start_at.astimezone(TZ) for a in upcoming] == [tomorrow_at(10)]
        assert upcoming[0].note == "làm tóc"
        assert "dời" in out["answer"].lower()
        assert "anh Hùng" in out["answer"]
        assert "10 giờ sáng" in out["answer"]

    async def test_a_taken_new_time_keeps_the_old_appointment(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        other = await AuthService(test_db).create_user("0938111222", "x", "Cô Hoa")
        old = await AppointmentService(test_db).create(user, tomorrow_at(9), "làm tóc")
        await AppointmentService(test_db).create(other, tomorrow_at(10), None)
        pending = {"start_at": tomorrow_at(10).isoformat(), "note": None,
                   "replaces_appointment_id": str(old.id)}

        out = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

        assert "có người" in out["answer"].lower()
        upcoming = await AppointmentService(test_db).upcoming_for(user)
        assert [a.start_at.astimezone(TZ) for a in upcoming] == [tomorrow_at(9)]
