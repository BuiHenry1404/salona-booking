# Task 5 · Sáu tool bọc service

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/tools.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `AppointmentService`, `ShopService` (Plan 1), `ConversationService` (task 3), `format_vi_datetime` (task 4), `parse_vi_time` (task 4b)
- Produces:
  - `make_status_tools(db, user) -> list[BaseTool]` — 1 tool
  - `make_booking_tools(db, user) -> list[BaseTool]` — 5 tool
  - Tên tool: `get_shop_status`, `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment`
  - **Không có tool ghi lịch.** Lịch chỉ được tạo ở node `confirm` (task 8), sau khi khách đồng ý.

- [x] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_tools.py`:

```python
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
    assert "3:00 chiều" in result   # 15h giờ Việt Nam, không bị lệch 7 tiếng


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
```

`test_parse_time_says_what_is_missing_instead_of_guessing` chạm tới tầng LLM, nên nó cần Azure. Nếu chưa cấu hình key, `parse_vi_time` fail-soft trả `missing=["giờ cụ thể"]` — test vẫn xanh, đúng ý: điều được khẳng định là "không đoán", không phải "hiểu được câu".

- [x] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.tools'`

- [x] **Step 3: Viết `app/agents/booking_graph/tools.py`**

```python
from datetime import date, datetime
from typing import List, Optional

from langchain_core.tools import BaseTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.core.clock import TZ, now_utc, to_local
from app.core.errors import AppError
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.shop import ShopService

# Đủ để phủ trọn một ngày ở bước 15 phút (96 mốc), có dư.
WHOLE_DAY = 200


def _parse_local(value: str) -> datetime:
    """Đọc chuỗi ISO và LUÔN trả về datetime có múi giờ.

    Lớp chốt trong `timeparse` đã gắn múi giờ, nhưng `propose_appointment` vẫn
    nhận chuỗi từ model — nó có thể bỏ qua `parse_time` rồi tự dựng ISO. Thiếu
    offset thì `fromisoformat` cho datetime naive, đem so với `now_utc()`
    tz-aware là TypeError, mà tool chỉ bắt ValueError và AppError.

    Thiếu offset thì hiểu là giờ Việt Nam: khách và tiệm ở cùng một chỗ.
    """
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed


def make_status_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    shop = ShopService(db)

    @tool
    async def get_shop_status() -> str:
        """Xem chủ tiệm đang bận hay đang rảnh, và nếu bận thì mấy giờ xong."""
        status = await shop.get_status()
        if not status.is_busy:
            return "Chủ tiệm đang rảnh."
        # Nói MỘT mốc giờ, không nói "còn N phút" — giống hệt thẻ trạng thái
        # trên giao diện. Nói hai kiểu ở hai chỗ là khách tưởng hai thông tin
        # khác nhau. Và câu trả lời của AI còn nằm lại trong lịch sử chat: "còn
        # 30 phút" đọc lại sau một tiếng là sai hẳn, "3:30 chiều" thì vẫn đúng.
        return f"Chủ tiệm đang bận, xong lúc {format_vi_datetime(status.busy_until)}."

    return [get_shop_status]


def make_booking_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".
    """
    service = AppointmentService(db)
    conversations = ConversationService(db)

    @tool
    async def parse_time(text: str) -> str:
        """Quy câu nói về thời gian của khách ra ngày giờ chuẩn.
        Gọi tool này TRƯỚC find_free_slots và propose_appointment, mỗi khi khách
        nhắc tới thời gian. Không tự tính ngày.
        Ví dụ text: "mai 3h chiều", "thứ Năm tuần sau", "sáng mai"."""
        parsed: ParsedTime = await parse_vi_time(text, now_utc())
        # Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
        # để chuyển thẳng sang propose_appointment, không được diễn giải lại.
        return parsed.model_dump_json(
            include={"start_at", "partial_date", "missing"}, exclude_none=False
        )

    @tool
    async def find_free_slots(day: str) -> str:
        """Xem các giờ còn trống trong một ngày. `day` theo định dạng YYYY-MM-DD,
        tính từ mốc "Bây giờ là..." trong phần bối cảnh — không được đoán ngày."""
        try:
            slots = await service.find_free_slots(date.fromisoformat(day))
        except ValueError:
            return "Ngày không hợp lệ, cần dạng YYYY-MM-DD."
        if not slots:
            return "Ngày đó không còn giờ trống."
        return "Các giờ còn trống: " + ", ".join(format_vi_datetime(s) for s in slots)

    @tool
    async def propose_appointment(start_at: str, note: Optional[str] = None) -> str:
        """Giữ chỗ tạm thời và chuẩn bị câu hỏi xác nhận cho khách.
        `start_at` dạng ISO 8601, lấy NGUYÊN từ kết quả parse_time.
        Gọi tool này rồi hỏi khách xác nhận. KHÔNG có tool nào ghi lịch trực tiếp —
        lịch chỉ được ghi khi khách trả lời đồng ý ở lượt sau."""
        try:
            start = _parse_local(start_at)
        except ValueError:
            return "Thời gian không hợp lệ."

        # Kiểm trước khi hỏi khách. Hỏi "3 giờ chiều đúng không cô?" rồi mới báo
        # giờ đó có người là bắt khách chọn lại hai lần.
        # `find_free_slots` lọc sẵn cả quá khứ, ngoài giờ mở cửa, ngày nghỉ và
        # giờ đã có người — một truy vấn thay cho bốn lần kiểm tay.
        #
        # `limit` phải phủ TRỌN ngày. Mặc định của service là 12 mốc, tức chỉ
        # tới gần 11 giờ trưa; để nguyên thì mọi giờ chiều đều bị báo "không đặt
        # được" dù còn trống.
        free = await service.find_free_slots(to_local(start).date(), limit=WHOLE_DAY)
        if start not in free:
            if not free:
                return "Ngày đó không còn giờ trống. Hãy hỏi khách chọn ngày khác."
            # Gợi ý ba mốc GẦN giờ khách xin nhất, không phải ba mốc đầu ngày:
            # khách xin 4 giờ chiều mà gợi ý 8, 8:15, 8:30 sáng là gợi ý vô ích.
            gan_nhat = sorted(sorted(free, key=lambda s: abs(s - start))[:3])
            goi_y = ", ".join(format_vi_datetime(s) for s in gan_nhat)
            return f"Giờ đó không đặt được. Các giờ còn trống gần nhất: {goi_y}."

        # Lưu vào Mongo để lượt sau đọc lại. Đây là điểm mấu chốt: giá trị đem đi
        # ghi lịch lấy từ DB, KHÔNG phải từ chuỗi model gõ lại — nên model không
        # thể chép sai giờ giữa hai lượt.
        await conversations.set_pending(
            str(user.id), {"start_at": start.isoformat(), "note": note}
        )
        note_text = f", {note}" if note else ""
        return (
            f"Đã giữ chỗ {format_vi_datetime(start)}{note_text}. "
            "Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận."
        )

    @tool
    async def list_my_appointments() -> str:
        """Xem các lịch sắp tới của chính khách đang chat. Gọi tool này trước khi
        hủy lịch, để lấy đúng mã lịch."""
        appointments = await service.upcoming_for(user)
        if not appointments:
            return "Khách chưa có lịch nào sắp tới."
        lines = []
        for appt in appointments:
            note_text = f" — {appt.note}" if appt.note else ""
            lines.append(f"{format_vi_datetime(appt.start_at)}{note_text} [id: {appt.id}]")
        return "\n".join(lines)

    @tool
    async def cancel_appointment(appointment_id: str) -> str:
        """Hủy một lịch. Phải gọi list_my_appointments trước để lấy mã lịch.
        Nếu khách có từ hai lịch trở lên, phải hỏi rõ hủy lịch nào."""
        try:
            await service.cancel(user, appointment_id)
        except AppError as exc:
            return exc.message
        return "Đã hủy lịch."

    return [
        parse_time,
        find_free_slots,
        propose_appointment,
        list_my_appointments,
        cancel_appointment,
    ]
```

- [x] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_tools.py -v`
Expected: PASS (16 passed) — quan trọng nhất là `test_there_is_NO_tool_that_writes_an_appointment` và `test_propose_stores_the_time_in_mongo_not_in_the_prompt`

- [x] **Step 5: Commit**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py
git commit -m "feat: five booking tools wrapping services, user_id closed over"
```
