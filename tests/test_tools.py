from datetime import datetime, timedelta

import pytest

from app.agents.booking_graph.tools import make_booking_tools, make_status_tools
from app.core.clock import TZ
from app.models.user import User
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def by_name(tools, name):
    return next(t for t in tools if t.name == name)


async def test_tool_names_are_exactly_as_specified(test_db):
    user = await a_user(test_db)
    assert [t.name for t in make_status_tools(test_db, user)] == ["get_shop_status"]
    assert sorted(t.name for t in make_booking_tools(test_db, user)) == [
        "cancel_appointment", "find_free_slots", "list_my_appointments",
        "parse_time", "propose_appointment",
    ]


async def test_there_is_NO_tool_that_writes_an_appointment(test_db):
    """Ràng buộc cấu trúc, không phải lời dặn trong prompt.

    Spec bắt "luôn nhắc lại ngày giờ cho khách xác nhận rồi mới ghi". Cách duy
    nhất khiến điều đó luôn đúng là không cấp cho model tool nào ghi được lịch —
    chỉ node `confirm` mới gọi service, sau khi khách đã đồng ý.
    """
    user = await a_user(test_db)
    names = {t.name for t in make_booking_tools(test_db, user)}
    assert "create_appointment" not in names


async def test_shop_status_reads_free(test_db):
    user = await a_user(test_db)
    tool = by_name(make_status_tools(test_db, user), "get_shop_status")
    assert "rảnh" in (await tool.ainvoke({})).lower()


async def test_shop_status_reads_busy_with_finish_time(test_db):
    """Giờ xong là thông tin chính. Mốc cố định thì không cũ đi, còn "còn 30
    phút" thì sai ngay sau đó — mà khách hay đọc lại tin nhắn cũ."""
    from app.services.shop import ShopService

    user = await a_user(test_db)
    await ShopService(test_db).set_busy(30)
    tool = by_name(make_status_tools(test_db, user), "get_shop_status")
    result = await tool.ainvoke({})

    assert "bận" in result.lower()
    assert "xong lúc" in result.lower()
    # KHÔNG được nói "còn 30 phút": câu này nằm lại trong lịch sử chat, đọc lại
    # sau một tiếng là sai hẳn.
    assert "30 phút" not in result


async def test_propose_stores_the_time_in_mongo_not_in_the_prompt(test_db):
    """ĐÂY LÀ ĐIỂM MẤU CHỐT của cả cơ chế.

    Giờ đã parse được cất vào `conversations`, nên lượt sau node `confirm` đọc
    lại từ DB. Nếu không có bước này, giá trị duy nhất còn tồn tại là chuỗi ISO
    nằm trong context của model — và model chép sai 15:00 thành 5:00 thì không
    gì phát hiện được.
    """
    from app.services.conversation import ConversationService

    user = await a_user(test_db)
    start = tomorrow_at(15)

    result = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": start.isoformat(), "note": "làm tóc"}
    )
    assert "giữ chỗ" in result.lower()

    pending = await ConversationService(test_db).get_pending(str(user.id))
    assert pending["start_at"] == start.isoformat()
    assert pending["note"] == "làm tóc"


async def test_propose_does_NOT_create_the_appointment(test_db):
    """Giữ chỗ khác với ghi lịch. Khách chưa đồng ý thì chưa có lịch nào."""
    user = await a_user(test_db)
    await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}
    )

    from app.services.appointment import AppointmentService
    assert await AppointmentService(test_db).upcoming_for(user) == []


async def test_propose_checks_availability_BEFORE_asking_the_customer(test_db):
    """Hỏi "3 giờ chiều đúng không cô?" rồi mới báo giờ đó có người là bắt khách
    chọn lại hai lần."""
    owner = await a_user(test_db)
    other = await a_user(test_db, phone="0938111222", name="Cô Hoa")

    from app.services.appointment import AppointmentService
    await AppointmentService(test_db).create(owner, tomorrow_at(16), note=None)

    result = await by_name(make_booking_tools(test_db, other), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(16).isoformat(), "note": None}
    )
    assert "không đặt được" in result.lower()
    assert "còn trống" in result.lower()   # phải gợi ý giờ khác, không bỏ lửng


async def test_propose_rejects_a_time_outside_opening_hours(test_db):
    user = await a_user(test_db)
    result = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(3).isoformat(), "note": None}   # 3 giờ sáng
    )
    assert "không đặt được" in result.lower()


async def test_list_then_cancel(test_db):
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)

    from app.services.appointment import AppointmentService
    await AppointmentService(test_db).create(user, tomorrow_at(15), note="làm tóc")

    listed = await by_name(tools, "list_my_appointments").ainvoke({})
    assert "làm tóc" in listed

    appointment_id = listed.split("[id:")[1].split("]")[0].strip()
    cancelled = await by_name(tools, "cancel_appointment").ainvoke({"appointment_id": appointment_id})
    assert "hủy" in cancelled.lower()

    assert "chưa có lịch" in (await by_name(tools, "list_my_appointments").ainvoke({})).lower()


async def test_tool_cannot_touch_another_users_appointment(test_db):
    """user_id đóng kín trong closure, không phải tham số — AI không thể bị dụ."""
    owner = await a_user(test_db)
    intruder = await a_user(test_db, phone="0938111222", name="Người lạ")

    from app.services.appointment import AppointmentService
    await AppointmentService(test_db).create(owner, tomorrow_at(17), note=None)

    listed = await by_name(make_booking_tools(test_db, owner), "list_my_appointments").ainvoke({})
    appointment_id = listed.split("[id:")[1].split("]")[0].strip()

    result = await by_name(make_booking_tools(test_db, intruder), "cancel_appointment").ainvoke(
        {"appointment_id": appointment_id}
    )
    assert "chỉ hủy được lịch của chính mình" in result.lower()


async def test_free_slots_never_include_3am(test_db):
    user = await a_user(test_db)
    tool = by_name(make_booking_tools(test_db, user), "find_free_slots")
    day = (datetime.now(TZ) + timedelta(days=1)).date().isoformat()
    assert "3:00 sáng" not in await tool.ainvoke({"day": day})


async def test_iso_without_timezone_is_read_as_vietnam_time(test_db):
    """LLM rất hay bỏ offset. Không vá thì `fromisoformat` cho datetime naive,
    so với `now_utc()` tz-aware là TypeError — mà tool chỉ bắt ValueError."""
    user = await a_user(test_db)
    naive = tomorrow_at(15).replace(tzinfo=None).isoformat()  # "2026-...T15:00:00"

    result = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": naive, "note": "làm tóc"}
    )
    assert "giữ chỗ" in result.lower()
    assert "3 giờ chiều" in result   # 15h giờ Việt Nam, không bị lệch 7 tiếng


async def test_rubbish_time_string_gets_a_polite_answer_not_a_crash(test_db):
    user = await a_user(test_db)
    result = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": "mai 3 giờ chiều", "note": None}
    )
    assert "không hợp lệ" in result.lower()


async def test_parse_time_returns_json_the_agent_can_hand_straight_on(test_db):
    """Trả JSON chứ không trả câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
    để chuyển thẳng sang propose_appointment, không được diễn giải lại."""
    import json

    user = await a_user(test_db)
    result = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
        {"text": "mai 3h chiều"}
    )
    payload = json.loads(result)

    assert payload["start_at"].endswith("+07:00")
    assert payload["start_at"][11:16] == "15:00"
    assert payload["missing"] == []


async def test_parse_time_says_what_is_missing_instead_of_guessing(test_db):
    """"sáng mai" thiếu giờ. Đoán thay khách là kiểu hỏng tệ nhất — cụ già
    tới tiệm lúc không ai mở cửa."""
    import json

    user = await a_user(test_db)
    result = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
        {"text": "sáng mai"}
    )
    payload = json.loads(result)

    assert payload["start_at"] is None
    assert payload["missing"]


async def test_parse_time_is_not_given_to_the_status_agent(test_db):
    """"chủ tiệm rảnh không" chẳng có gì để parse. Cấp thừa tool là thêm chỗ
    cho model gọi nhầm và tốn thêm một lượt."""
    user = await a_user(test_db)
    names = {t.name for t in make_status_tools(test_db, user)}
    assert "parse_time" not in names


async def test_tool_descriptions_are_english(test_db):
    """Docstring của tool đi vào tool schema gửi cho model — nó là prompt.
    CONTEXT.md:94: prompt viết tiếng Anh, câu mẫu giữ tiếng Việt."""
    user = await a_user(test_db)
    # Gộp khoảng trắng: docstring xuống dòng giữa câu, so chuỗi thô thì một cụm
    # bị ngắt dòng sẽ không khớp dù nội dung đúng.
    descriptions = {
        t.name: " ".join(t.description.split())
        for t in make_booking_tools(test_db, user)
    }

    assert descriptions["parse_time"].startswith("Turn what the customer said")
    assert "Call this BEFORE find_free_slots" in descriptions["parse_time"]
    # Ví dụ PHẢI còn tiếng Việt — dịch đi thì ví dụ vô nghĩa.
    assert "mai 3h chiều" in descriptions["parse_time"]
    assert "NO tool writes an appointment directly" in descriptions["propose_appointment"]
