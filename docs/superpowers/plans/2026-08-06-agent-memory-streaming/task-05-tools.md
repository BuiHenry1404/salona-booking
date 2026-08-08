# Task 5 · Sáu tool bọc service

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/tools.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `AppointmentService`, `ShopService` (Plan 1), `format_vi_datetime` (task 4), `parse_vi_time` (task 4b)
- Produces:
  - `make_status_tools(db, user) -> list[BaseTool]` — 1 tool
  - `make_booking_tools(db, user) -> list[BaseTool]` — 5 tool
  - Tên tool: `get_shop_status`, `parse_time`, `find_free_slots`, `create_appointment`, `list_my_appointments`, `cancel_appointment`

- [ ] **Step 1: Viết test (sẽ fail)**

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
        "cancel_appointment", "create_appointment", "find_free_slots",
        "list_my_appointments", "parse_time",
    ]


async def test_shop_status_reads_free(test_db):
    user = await a_user(test_db)
    tool = by_name(make_status_tools(test_db, user), "get_shop_status")
    assert "rảnh" in (await tool.ainvoke({})).lower()


async def test_shop_status_reads_busy_with_minutes(test_db):
    from app.services.shop import ShopService

    user = await a_user(test_db)
    await ShopService(test_db).set_busy(30)
    tool = by_name(make_status_tools(test_db, user), "get_shop_status")
    result = await tool.ainvoke({})
    assert "bận" in result.lower() and "30" in result


async def test_create_then_list_then_cancel(test_db):
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)

    created = await by_name(tools, "create_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}
    )
    assert "làm tóc" in created

    listed = await by_name(tools, "list_my_appointments").ainvoke({})
    assert "làm tóc" in listed

    appointment_id = listed.split("[id:")[1].split("]")[0].strip()
    cancelled = await by_name(tools, "cancel_appointment").ainvoke({"appointment_id": appointment_id})
    assert "hủy" in cancelled.lower()

    assert "chưa có lịch" in (await by_name(tools, "list_my_appointments").ainvoke({})).lower()


async def test_create_at_a_taken_time_returns_a_friendly_message(test_db):
    owner = await a_user(test_db)
    other = await a_user(test_db, phone="0938111222", name="Cô Hoa")

    await by_name(make_booking_tools(test_db, owner), "create_appointment").ainvoke(
        {"start_at": tomorrow_at(16).isoformat(), "note": None}
    )
    result = await by_name(make_booking_tools(test_db, other), "create_appointment").ainvoke(
        {"start_at": tomorrow_at(16).isoformat(), "note": None}
    )
    assert "có người" in result.lower()


async def test_tool_cannot_touch_another_users_appointment(test_db):
    """user_id đóng kín trong closure, không phải tham số — AI không thể bị dụ."""
    owner = await a_user(test_db)
    intruder = await a_user(test_db, phone="0938111222", name="Người lạ")

    await by_name(make_booking_tools(test_db, owner), "create_appointment").ainvoke(
        {"start_at": tomorrow_at(17).isoformat(), "note": None}
    )
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

    result = await by_name(make_booking_tools(test_db, user), "create_appointment").ainvoke(
        {"start_at": naive, "note": "làm tóc"}
    )
    assert "đã ghi lịch" in result.lower()
    assert "3:00 chiều" in result   # 15h giờ Việt Nam, không bị lệch 7 tiếng


async def test_rubbish_time_string_gets_a_polite_answer_not_a_crash(test_db):
    user = await a_user(test_db)
    result = await by_name(make_booking_tools(test_db, user), "create_appointment").ainvoke(
        {"start_at": "mai 3 giờ chiều", "note": None}
    )
    assert "không hợp lệ" in result.lower()


async def test_parse_time_returns_json_the_agent_can_hand_straight_on(test_db):
    """Trả JSON chứ không trả câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
    để chuyển thẳng sang create_appointment, không được diễn giải lại."""
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

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.tools'`

- [ ] **Step 3: Viết `app/agents/booking_graph/tools.py`**

```python
from datetime import date, datetime
from typing import List, Optional

from langchain_core.tools import BaseTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.core.clock import TZ, now_utc
from app.core.errors import AppError
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.shop import ShopService


def _parse_local(value: str) -> datetime:
    """Đọc chuỗi ISO và LUÔN trả về datetime có múi giờ.

    Lớp chốt trong `timeparse` đã gắn múi giờ, nhưng `create_appointment` vẫn
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
        """Xem chủ tiệm đang bận hay đang rảnh, và nếu bận thì còn bao lâu nữa xong."""
        status = await shop.get_status()
        if not status.is_busy:
            return "Chủ tiệm đang rảnh."
        return f"Chủ tiệm đang bận, còn khoảng {status.minutes_left} phút nữa xong."

    return [get_shop_status]


def make_booking_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".
    """
    service = AppointmentService(db)

    @tool
    async def parse_time(text: str) -> str:
        """Quy câu nói về thời gian của khách ra ngày giờ chuẩn.
        Gọi tool này TRƯỚC find_free_slots và create_appointment, mỗi khi khách
        nhắc tới thời gian. Không tự tính ngày.
        Ví dụ text: "mai 3h chiều", "thứ Năm tuần sau", "sáng mai"."""
        parsed: ParsedTime = await parse_vi_time(text, now_utc())
        # Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
        # để chuyển thẳng sang create_appointment, không được diễn giải lại.
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
    async def create_appointment(start_at: str, note: Optional[str] = None) -> str:
        """Ghi lịch hẹn cho khách. `start_at` dạng ISO 8601, ví dụ 2026-08-08T15:00:00.
        Ngày phải tính từ mốc "Bây giờ là..." trong phần bối cảnh, không được đoán.
        Chỉ gọi sau khi khách đã xác nhận rõ ngày giờ."""
        try:
            appt = await service.create(user, _parse_local(start_at), note)
        except ValueError:
            return "Thời gian không hợp lệ."
        except AppError as exc:
            return exc.message
        note_text = f", {appt.note}" if appt.note else ""
        return f"Đã ghi lịch {format_vi_datetime(appt.start_at)}{note_text}."

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
        create_appointment,
        list_my_appointments,
        cancel_appointment,
    ]
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_tools.py -v`
Expected: PASS (12 passed) — quan trọng nhất là `test_tool_cannot_touch_another_users_appointment` và `test_parse_time_says_what_is_missing_instead_of_guessing`

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py
git commit -m "feat: five booking tools wrapping services, user_id closed over"
```
