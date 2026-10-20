# Tool cấp dữ kiện, không viết sẵn câu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi 6 tool trong `app/agents/booking_graph/tools.py` từ trả câu tiếng Việt sang trả JSON dữ kiện, chuyển mọi mệnh lệnh đang nằm trong tool về `prompts.py`, và đổi câu lỗi thành mã lỗi.

**Architecture:** Mỗi tool trả một chuỗi JSON. Mốc thời gian luôn là object hai trường `{"iso", "spoken"}` — `spoken` do `format_vi_datetime`/`format_vi_hhmm` sinh y như hiện nay, nên `guard.check_clock` và luật "chép y giờ tool viết" vẫn còn hiệu lực. Cái mất đi là **khuôn câu**, không phải dạng đọc. `parse_time` giữ nguyên vì đã đúng khuôn từ đầu.

**Tech Stack:** Python 3.12, LangChain tools, Motor (MongoDB), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-20-tools-return-data-design.md`

## Global Constraints

- Nhánh: `refactor/tools-return-data`, rẽ từ `fix/live-run-findings`. Không merge, không push, không checkout nhánh khác.
- **Cấm `git add -A` và `git add .`** — stage đúng đường dẫn. Working tree có 4 file `.txt` bị xoá (`after-digest*.txt`, `after-voice-*.txt`) và các file transcript `hoi-thoai-dai-*.txt` **không thuộc việc này** — đừng chạm.
- Chạy test: `PYTHONPATH=. .venv/bin/python -m pytest -q` từ gốc repo. **Chỉ MỘT tiến trình pytest một lúc** — hai tiến trình tranh chấp cùng Mongo test DB và ra hàng chục lỗi pymongo giả. Mốc bắt đầu: **785 passed, 1 skipped, 14 deselected, 0 failed**. Cái skip là `tests/test_telegram_handlers.py:130`, tự skip sau 3 giờ chiều giờ VN — bình thường.
- **`AppError.message` KHÔNG được đổi** — nó phục vụ tầng REST và frontend. Tool ánh xạ exception sang mã lỗi theo **LỚP**, không theo chuỗi `message`.
- **`parse_time` không đụng tới.**
- **`confirm.py`, `FALLBACK_ANSWER`, `build_context_block`, `guard.check_clock` không đụng tới.**
- Xưng hô: "em"/"anh"/"chị". Cấm "con", "cô", "chú", "bác" ở mọi chuỗi khách thấy được.
- Docstring của tool viết **tiếng Anh** (quy ước từ 2026-09-14). Comment trong code viết **tiếng Việt**, giải thích *tại sao*.
- Commit message kết thúc bằng: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Mỗi task: viết test trước → chạy cho ĐỎ → sửa code → chạy XANH → chạy cả bộ → commit.

## File Structure

| File | Trách nhiệm |
|---|---|
| `app/agents/booking_graph/tools.py` (sửa) | 6 tool trả JSON; helper `_moment()`; ánh xạ mã lỗi |
| `app/agents/booking_graph/prompts.py` (sửa) | Nhận ba mệnh lệnh chuyển từ tool sang; mô tả hợp đồng JSON mới |
| `tests/test_tools.py` (sửa) | Đổi assert theo hợp đồng mới — **đổi, không nới** |
| `tests/test_prompts.py` (sửa) | Luật mới có mặt |
| `tests/test_graph_wiring.py` (sửa) | Hàng rào: `tools.py` không còn chuỗi mệnh lệnh |
| `scripts/llm_scenarios.py` | Không sửa; chỉ chạy lại |

---

### Task 1: Helper `_moment()` và hai tool của shop

Task nhỏ nhất, dựng sẵn khuôn cho bốn tool sau.

**Files:**
- Modify: `app/agents/booking_graph/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `_moment(dt: datetime) -> dict` trả `{"iso": dt.isoformat(), "spoken": format_vi_datetime(dt)}`; `get_shop_status` và `get_shop_hours` trả chuỗi JSON.

- [ ] **Step 1: Chụp mốc xanh**

```bash
cd /home/henryb1/Desktop/HenryB1/data/salona-booking
git checkout -b refactor/tools-return-data
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Kỳ vọng `785 passed, 1 skipped, 14 deselected`. Đỏ sẵn thì **dừng và báo**.

- [ ] **Step 2: Viết test đỏ**

Trong `tests/test_tools.py`, **thay** `test_shop_status_reads_free` và `test_shop_status_reads_busy_with_finish_time`:

```python
async def test_shop_status_returns_data_not_a_sentence(test_db):
    tools = make_shop_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "get_shop_status").ainvoke({}))
    assert out == {"busy": False}


async def test_shop_status_busy_carries_both_forms_of_the_finish_time(test_db):
    """`iso` để máy đọc, `spoken` để model chép y — guard.check_clock cờ mọi
    chuỗi HH:MM nên model KHÔNG được tự định dạng giờ."""
    user = await a_user(test_db)
    until = tomorrow_at(15)
    await ShopService(test_db).set_busy(until)
    tools = make_shop_tools(test_db, user)
    out = json.loads(await by_name(tools, "get_shop_status").ainvoke({}))

    assert out["busy"] is True
    assert out["until"]["iso"] == until.isoformat()
    assert out["until"]["spoken"] == format_vi_datetime(until)
    # Mốc giờ tuyệt đối, không đếm ngược — câu đáp nằm lại trong lịch sử chat.
    assert "phút" not in json.dumps(out, ensure_ascii=False)


async def test_shop_hours_returns_data_not_a_sentence(test_db):
    tools = make_shop_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "get_shop_hours").ainvoke({}))

    assert out["open"]["iso"] == "08:00"
    assert out["open"]["spoken"] == format_vi_hhmm("08:00")
    assert out["close"]["iso"] == "19:00"
    assert out["closed_days"] == []
    # Không còn câu nào: "Mở cả tuần." là kết luận của model, không phải của tool.
    assert "Mở cả tuần" not in json.dumps(out, ensure_ascii=False)
```

Thêm `import json` ở đầu file nếu chưa có, và import `format_vi_datetime`, `format_vi_hhmm`, `ShopService` nếu chưa có. Kiểm tra chữ ký thật của hàm đặt bận trong `ShopService` trước khi dùng (`grep -n "def set_busy\|def set_status" app/services/shop.py`) và dùng đúng tên có sẵn.

- [ ] **Step 3: Chạy cho đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
```

Kỳ vọng FAIL với `json.decoder.JSONDecodeError` — tool đang trả câu tiếng Việt.

- [ ] **Step 4: Viết helper và sửa hai tool**

Thêm ở đầu `tools.py`, dưới `_parse_local`:

```python
def _moment(dt: datetime) -> dict:
    """Một mốc thời gian, cấp CẢ HAI dạng.

    `iso` để model chuyền tiếp sang tool khác không diễn giải lại. `spoken` để
    model chép y vào câu nói với khách: `guard.check_clock` cờ mọi chuỗi
    `HH:MM` và mọi số viết bằng chữ, nên nếu tool không cấp dạng đọc thì model
    phải tự chế ra — đúng thứ guard sinh ra để chặn.

    Đây là chỗ DUY NHẤT tool được phép sinh chữ tiếng Việt. Mọi thứ khác là
    dữ kiện; câu thì model tự viết.
    """
    return {"iso": dt.isoformat(), "spoken": format_vi_datetime(dt)}
```

Thêm `import json` vào đầu file. Sửa hai tool:

```python
    @tool
    async def get_shop_status() -> str:
        """Whether the owner is busy or free, and if busy, what time they finish.
        Returns JSON. When `busy` is true, say the finish time using
        `until.spoken` exactly as written — never a countdown in minutes."""
        status = await shop.get_status()
        if not status.is_busy:
            return json.dumps({"busy": False})
        # Mốc giờ tuyệt đối, không "còn N phút": câu đáp nằm lại trong lịch sử
        # chat và đọc lại sau một tiếng là sai hẳn.
        return json.dumps({"busy": True, "until": _moment(status.busy_until)},
                          ensure_ascii=False)

    @tool
    async def get_shop_hours() -> str:
        """Opening hours and closed days, as JSON. Call this when the customer
        asks what time the salon opens or closes, or whether it is open on a
        given day. Say the times using `open.spoken` and `close.spoken` exactly
        as written. An empty `closed_days` means the salon opens every day."""
        hours = await shop.get_hours()
        closed = [_CLOSED_DAY_NAMES[d] for d in sorted(hours.closed_days)
                  if 0 <= d < len(_CLOSED_DAY_NAMES)]
        return json.dumps({
            "open": {"iso": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
            "close": {"iso": hours.close_time, "spoken": format_vi_hhmm(hours.close_time)},
            "closed_days": closed,
        }, ensure_ascii=False)
```

`ensure_ascii=False` bắt buộc: thiếu nó thì tiếng Việt thành `ả...` trong prompt, vừa tốn token vừa khó đọc lúc soi trace.

- [ ] **Step 5: Chạy test của task, rồi cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Cả hai PASS. Nếu `tests/test_subagents.py` hay `tests/test_graph_events.py` đỏ vì chờ chuỗi cũ, sửa chúng theo hợp đồng mới — **đổi assert, không nới assert**.

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py
git commit -m "refactor: hai tool của shop trả JSON thay vì câu tiếng Việt

_moment() cấp cả `iso` lẫn `spoken`: guard.check_clock cờ mọi HH:MM nên
model không được tự định dạng giờ, nhưng câu thì nó tự viết.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `find_free_slots` và `list_my_appointments`

Hai tool chỉ đọc, chưa đụng mã lỗi phức tạp.

**Files:**
- Modify: `app/agents/booking_graph/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `_moment()` (Task 1).
- Produces: `find_free_slots` → `{"day": "YYYY-MM-DD", "slots": [moment]}` hoặc `{"error": "bad_day_format"}`; `list_my_appointments` → `{"appointments": [{"id", "at": moment, "note"}]}`.

- [ ] **Step 1: Viết test đỏ**

Thay `test_free_slots_never_include_3am` và phần liệt kê của `test_list_then_cancel`:

```python
async def test_free_slots_returns_data_not_a_sentence(test_db):
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)
    day = tomorrow_at(9).date().isoformat()
    out = json.loads(await by_name(tools, "find_free_slots").ainvoke({"day": day}))

    assert out["day"] == day
    assert out["slots"], "ngày trống phải có mốc"
    assert all(set(s) == {"iso", "spoken"} for s in out["slots"])
    # 3 giờ sáng ngoài giờ mở cửa — không mốc nào được rơi vào đó.
    assert not any(s["iso"].split("T")[1].startswith("03:") for s in out["slots"])


async def test_free_slots_on_a_full_day_returns_an_empty_list_not_a_sentence(test_db):
    """Hết chỗ là DỮ KIỆN (`slots: []`), không phải câu "Ngày đó không còn giờ
    trống." — câu đó là việc của model."""
    tools = make_booking_tools(test_db, await a_user(test_db))
    # Ngày Chủ Nhật xa trong quá khứ: find_free_slots lọc sạch mốc quá khứ.
    out = json.loads(await by_name(tools, "find_free_slots").ainvoke({"day": "2020-01-01"}))
    assert out["slots"] == []


async def test_free_slots_rejects_a_bad_day_with_a_code_not_a_sentence(test_db):
    tools = make_booking_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "find_free_slots").ainvoke({"day": "mai"}))
    assert out == {"error": "bad_day_format"}


async def test_list_my_appointments_returns_data_not_a_sentence(test_db):
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)
    when = tomorrow_at(9)
    appt = await AppointmentService(test_db).create(user, when, note="làm tóc")

    out = json.loads(await by_name(tools, "list_my_appointments").ainvoke({}))
    assert len(out["appointments"]) == 1
    row = out["appointments"][0]
    assert row["id"] == str(appt.id)
    assert row["note"] == "làm tóc"
    assert row["at"]["spoken"] == format_vi_datetime(when)


async def test_no_appointments_is_an_empty_list_not_a_sentence(test_db):
    tools = make_booking_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "list_my_appointments").ainvoke({}))
    assert out == {"appointments": []}
```

Trước khi viết, kiểm chữ ký thật của hàm tạo lịch: `grep -n "async def create" -A6 app/services/appointment.py`, và dùng đúng cách gọi mà các test sẵn có trong file này đang dùng.

- [ ] **Step 2: Chạy cho đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
```

Kỳ vọng FAIL với `JSONDecodeError`.

- [ ] **Step 3: Sửa hai tool**

```python
    @tool
    async def find_free_slots(day: str) -> str:
        """Free slots on one day, as JSON. `day` is YYYY-MM-DD, taken from the
        "Bây giờ là..." line in the context block — never guess the date.
        Offer ONLY slots this tool returned; never invent one. Say each time
        using its `spoken` value exactly as written.
        An empty `slots` list means the day is full — ask them for another day.
        If the time they asked for is taken, offer the two slots nearest to it.
        Only when the customer says the salon may pick the time for them, call
        this for today — or tomorrow if today is finished — then offer two or
        three slots. If they have NOT named a day, ask which day first. Never
        assume today."""
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            return json.dumps({"error": "bad_day_format"})
        slots = await service.find_free_slots(parsed_day)
        return json.dumps({"day": day, "slots": [_moment(s) for s in slots]},
                          ensure_ascii=False)

    @tool
    async def list_my_appointments() -> str:
        """The customer's own upcoming appointments, as JSON, each with its id.
        The context block already lists them with the same ids; call this only
        when the appointment you need is not shown there.
        When the customer asks about their OWN appointments, call this
        IMMEDIATELY — never ask for a date first, the tool filters by the
        logged-in customer. An empty `appointments` list means they have none;
        say so plainly."""
        appointments = await service.upcoming_for(user)
        return json.dumps({"appointments": [
            {"id": str(a.id), "at": _moment(a.start_at), "note": a.note}
            for a in appointments
        ]}, ensure_ascii=False)
```

- [ ] **Step 4: Chạy test của task, rồi cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py
git commit -m "refactor: find_free_slots và list_my_appointments trả JSON

Hết chỗ và không có lịch giờ là DỮ KIỆN (mảng rỗng), không phải câu.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `propose_appointment` và `cancel_appointment` + ánh xạ mã lỗi

Hai tool giữ ý định ghi. Đây là chỗ mã lỗi thay câu lỗi.

**Files:**
- Modify: `app/agents/booking_graph/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `_moment()` (Task 1).
- Produces: `_error_code(exc: AppError) -> str`; `propose_appointment` → `{"held", "note", "replaces"?}` / `{"error": "slot_unavailable", "nearest": [...]}` / `{"error": "bad_time_format"}` / `{"error": "appointment_not_found"|"not_your_appointment"}`; `cancel_appointment` → `{"will_cancel": {"id", "at"}}` / `{"error": ...}`.

- [ ] **Step 1: Viết test đỏ**

Thay các test đang assert `"giữ chỗ"`, `"không đặt được"`, `"không hợp lệ"`, `"của chính mình"`, `"hủy"`:

```python
async def test_propose_returns_data_and_still_stores_the_time_in_mongo(test_db):
    """Chốt cũ không đổi: giá trị đem đi ghi lịch lấy từ Mongo, KHÔNG từ chuỗi
    model gõ lại. Đợt này chỉ đổi HÌNH DẠNG thứ tool trả về."""
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)
    when = tomorrow_at(15)
    out = json.loads(await by_name(tools, "propose_appointment").ainvoke(
        {"start_at": when.isoformat(), "note": "làm tóc"}))

    assert out["held"]["iso"] == when.isoformat()
    assert out["held"]["spoken"] == format_vi_datetime(when)
    assert out["note"] == "làm tóc"
    assert "replaces" not in out
    # Không còn mệnh lệnh nào lẫn trong kết quả tool.
    assert "Hãy" not in json.dumps(out, ensure_ascii=False)

    pending = await ConversationService(test_db).get_pending(str(user.id))
    assert pending["start_at"] == when.isoformat()


async def test_propose_on_a_taken_time_returns_a_code_and_nearest_slots(test_db):
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user)
    when = tomorrow_at(15)
    await AppointmentService(test_db).create(
        await a_user(test_db, phone="0911111111", name="Chị Hoa"), when, note="x")

    out = json.loads(await by_name(tools, "propose_appointment").ainvoke(
        {"start_at": when.isoformat()}))

    assert out["error"] == "slot_unavailable"
    assert out["nearest"], "phải gợi ý giờ khác, không bỏ lửng"
    assert all(set(s) == {"iso", "spoken"} for s in out["nearest"])


async def test_propose_rejects_rubbish_time_with_a_code(test_db):
    tools = make_booking_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "propose_appointment").ainvoke(
        {"start_at": "mai 3 giờ"}))
    assert out == {"error": "bad_time_format"}


async def test_another_users_appointment_maps_to_a_code_not_the_rest_message(test_db):
    """`AppError.message` viết cho tầng REST, không viết cho lễ tân. Ánh xạ
    theo LỚP exception — chuỗi message đổi lúc nào không ai báo."""
    owner = await a_user(test_db, phone="0911111111", name="Chị Hoa")
    appt = await AppointmentService(test_db).create(owner, tomorrow_at(9), note="x")
    tools = make_booking_tools(test_db, await a_user(test_db))

    out = json.loads(await by_name(tools, "cancel_appointment").ainvoke(
        {"appointment_id": str(appt.id)}))
    assert out == {"error": "not_your_appointment"}


async def test_an_unknown_appointment_id_maps_to_a_code(test_db):
    tools = make_booking_tools(test_db, await a_user(test_db))
    out = json.loads(await by_name(tools, "cancel_appointment").ainvoke(
        {"appointment_id": "a" * 24}))
    assert out == {"error": "appointment_not_found"}


async def test_cancel_returns_data_and_still_only_records_the_intent(test_db):
    """Chốt cũ không đổi: tool KHÔNG hủy, chỉ ghi ý định; node confirm mới hủy."""
    user = await a_user(test_db)
    when = tomorrow_at(9)
    appt = await AppointmentService(test_db).create(user, when, note="làm tóc")
    tools = make_booking_tools(test_db, user)

    out = json.loads(await by_name(tools, "cancel_appointment").ainvoke(
        {"appointment_id": str(appt.id)}))

    assert out["will_cancel"]["id"] == str(appt.id)
    assert out["will_cancel"]["at"]["spoken"] == format_vi_datetime(when)
    # Vẫn còn nguyên trong danh sách — chưa hủy gì cả.
    still = json.loads(await by_name(tools, "list_my_appointments").ainvoke({}))
    assert len(still["appointments"]) == 1
```

- [ ] **Step 2: Chạy cho đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
```

- [ ] **Step 3: Viết ánh xạ mã lỗi**

Thêm dưới `_moment()`:

```python
# Ánh xạ theo LỚP exception, không theo chuỗi `message`: `AppError.message`
# viết cho tầng REST và frontend, đổi lúc nào không ai báo cho tầng này.
_ERROR_CODES = {
    NotFoundError: "appointment_not_found",
    ForbiddenError: "not_your_appointment",
}


def _error_code(exc: AppError) -> str:
    return _ERROR_CODES.get(type(exc), "unavailable")
```

Thêm `NotFoundError, ForbiddenError` vào dòng import từ `app.core.errors`.

- [ ] **Step 4: Sửa hai tool**

```python
    @tool
    async def propose_appointment(
        start_at: str,
        note: Optional[str] = None,
        replaces_appointment_id: Optional[str] = None,
    ) -> str:
        """Hold the slot temporarily, as JSON. `start_at` is ISO 8601, copied
        UNCHANGED from parse_time's result.
        NO tool writes an appointment directly — it is written only when the
        customer agrees on the NEXT turn.
        On success the result carries `held`; say `held.spoken` exactly as
        written. `error: "slot_unavailable"` carries `nearest`, the free slots
        closest to what they asked for; an empty `nearest` means the day is full.
        `note` is the service only (what they want done) — never the time or
        the day; those live in `start_at`.
        `replaces_appointment_id`: pass it ONLY when the customer wants to MOVE
        (reschedule) an appointment they already have. Copy the id from the
        context block. Leave it out for a brand-new booking."""
        # `note` do model gõ lại theo lời khách và được lưu vào
        # `pending_confirmation` — đường này không đi qua `Appointment`, nên
        # cap ở `Appointment._clean_note` không tự áp dụng. Lọc lại ở đây.
        note = single_line(note, NOTE_MAX)
        try:
            start = _parse_local(start_at)
        except ValueError:
            return json.dumps({"error": "bad_time_format"})

        # Dời lịch: kiểm id TRƯỚC khi giữ chỗ. Id bịa mà vẫn giữ chỗ thì khách
        # chốt "ừ" xong là ra hai lịch — đúng BUG-1.
        old = None
        if replaces_appointment_id:
            try:
                old = await service.own_booked(user, replaces_appointment_id)
            except AppError as exc:
                return json.dumps({"error": _error_code(exc)})

        # Kiểm trước khi hỏi khách. `find_free_slots` lọc sẵn quá khứ, ngoài
        # giờ mở cửa, ngày nghỉ và giờ đã có người — một truy vấn thay bốn lần
        # kiểm tay. `limit` phải phủ TRỌN ngày, mặc định 12 mốc chỉ tới gần
        # 11 giờ trưa. Đang dời thì mốc của chính lịch cũ tính là trống.
        free = await service.find_free_slots(
            to_local(start).date(), limit=WHOLE_DAY,
            ignore_appointment_id=str(old.id) if old else None,
        )
        if start not in free:
            # Ba mốc GẦN giờ khách xin nhất, không phải ba mốc đầu ngày: khách
            # xin 4 giờ chiều mà gợi ý 8 giờ sáng là gợi ý vô ích.
            nearest = sorted(sorted(free, key=lambda s: abs(s - start))[:3])
            return json.dumps({"error": "slot_unavailable",
                               "nearest": [_moment(s) for s in nearest]},
                              ensure_ascii=False)

        # Lưu vào Mongo để lượt sau đọc lại: giá trị đem đi ghi lịch lấy từ DB,
        # KHÔNG từ chuỗi model gõ lại.
        payload = {"start_at": start.isoformat(), "note": note}
        if old:
            payload["replaces_appointment_id"] = str(old.id)
        await conversations.set_pending(str(user.id), payload)

        result = {"held": _moment(start), "note": note}
        if old:
            result["replaces"] = {"id": str(old.id), "was": _moment(old.start_at)}
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def cancel_appointment(appointment_id: str) -> str:
        """Prepare to cancel one appointment, as JSON. Call this AS SOON AS the
        customer asks to cancel — do not ask them to confirm before calling it.
        Nothing is cancelled yet: the tool only records the intent, and the
        appointment is cancelled only when they agree on the NEXT turn. Never
        say it is already cancelled before that.
        `appointment_id` is the id on that appointment's line in the context
        block — copy it exactly. Never invent or guess an id; if the context
        block shows none, call list_my_appointments first. If the customer has
        two or more appointments, ask which one first."""
        # Không hủy ở đây. Hủy giống đặt: tool giữ ý định, node confirm mới làm
        # thật khi khách "ừ". Kiểm id trước để không giữ một ý hủy vô nghĩa.
        try:
            appt = await service.own_booked(user, appointment_id)
        except AppError as exc:
            return json.dumps({"error": _error_code(exc)})
        await conversations.set_pending(
            str(user.id),
            {"cancel_appointment_id": str(appt.id), "start_at": appt.start_at.isoformat()},
        )
        return json.dumps({"will_cancel": {"id": str(appt.id),
                                           "at": _moment(appt.start_at)}},
                          ensure_ascii=False)
```

**Chú ý một thay đổi hành vi có chủ ý:** bản cũ trả `"Ngày đó không còn giờ trống."` khi `free` rỗng và `"Giờ đó không đặt được..."` khi còn chỗ khác — hai câu khác nhau. Bản mới dùng **một** mã `slot_unavailable`, phân biệt bằng `nearest` rỗng hay không. Model đọc `nearest: []` là biết cả ngày hết chỗ.

- [ ] **Step 5: Chạy test của task, rồi cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -q
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py
git commit -m "refactor: propose và cancel trả JSON, câu lỗi thành mã lỗi

Ánh xạ theo LỚP exception, không theo chuỗi message — message viết cho
tầng REST, đổi lúc nào không ai báo cho tầng agent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Chuyển mệnh lệnh về prompt, dựng hàng rào

**Files:**
- Modify: `app/agents/booking_graph/prompts.py`, `app/agents/booking_graph/tools.py`
- Test: `tests/test_prompts.py`, `tests/test_graph_wiring.py`

**Interfaces:**
- Consumes: hợp đồng JSON của Task 1–3.
- Produces: luật mới trong `BOOKING_PROMPT`; `test_tools_return_data_not_instructions`.

- [ ] **Step 1: Viết hàng rào đỏ**

Thêm vào `tests/test_graph_wiring.py`:

```python
def test_tools_return_data_not_instructions():
    """Tool là chỗ cấp DỮ KIỆN. Trước 2026-09-20 nó nhét cả mệnh lệnh vào kết
    quả trả về ("Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận.") — chỉ
    dẫn cho model trá hình thành dữ liệu.

    Hậu quả: cùng một luật tồn tại hai bản, một ở prompts.py một ở tools.py,
    rồi trôi lệch nhau mà không ai rà được. Chỉ dẫn thuộc system prompt —
    ổn định và được cache; kết quả tool thì đổi mỗi lần gọi.
    """
    import inspect

    from app.agents.booking_graph import tools

    source = inspect.getsource(tools)
    # Chỉ soi phần THÂN HÀM, không soi docstring: docstring của tool ĐÚNG là
    # chỗ để chỉ dẫn, nó đi vào định nghĩa tool chứ không vào kết quả trả về.
    bodies = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith(('"""', "#", "'''"))
    )
    for cam in ("Hãy ", "hãy "):
        assert cam not in bodies, (
            f"tools.py còn mệnh lệnh {cam!r} trong thân hàm — chuyển sang prompts.py"
        )
```

Chạy: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_graph_wiring.py -q` — kỳ vọng ĐỎ nếu Task 3 còn sót chuỗi mệnh lệnh, XANH nếu đã sạch. **Nếu nó xanh ngay, vẫn giữ test** — đó là hàng rào chống tái diễn, và hãy nói rõ trong báo cáo là nó xanh ngay.

- [ ] **Step 2: Viết test đỏ cho luật mới trong prompt**

Thêm vào `tests/test_prompts.py`, trong `TestHardWonRulesSurvive`:

```python
    def test_the_rules_the_tools_used_to_carry_now_live_here(self):
        """Ba mệnh lệnh này trước nằm trong chuỗi trả về của tool. Chuyển sang
        prompt thì phải thật sự có mặt, không thì luật biến mất lặng lẽ."""
        assert "read the booking back" in BOOKING_PROMPT.lower()
        assert "ask them to confirm before cancelling" in BOOKING_PROMPT.lower()
        assert "another day" in BOOKING_PROMPT.lower()

    def test_the_prompt_describes_the_json_tool_contract(self):
        """Tool giờ trả JSON. Model phải biết đọc `spoken` chứ không chép cả
        khoá `iso` ra cho khách."""
        assert "spoken" in BOOKING_PROMPT
        assert "spoken" in SHOP_PROMPT
```

- [ ] **Step 3: Chạy cho đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_prompts.py -q
```

- [ ] **Step 4: Thêm luật vào prompt**

Trong `BOOKING_PROMPT`, sửa rule 2 (đọc lại lịch) cho nói theo hợp đồng mới và gộp luôn hai mệnh lệnh còn lại:

```
2. Every tool returns JSON, never a finished sentence. Write the sentence
   yourself. Take clock times and dates from the `spoken` field and copy that
   text exactly; never show the customer an `iso` value or a JSON key.
   After propose_appointment succeeds, read the booking back to the customer
   and ask them to confirm. Your sentence MUST contain the weekday and date,
   the clock time, and the service, and MUST end in a question.
   Vary the wording — a customer who books twice should not hear the same
   sentence twice. What is fixed is the content, not the words.
   After cancel_appointment returns, ask them to confirm before cancelling.
   When `nearest` or `slots` comes back empty, that day is full — ask them for
   another day.
```

Trong `SHOP_PROMPT`, sửa đoạn mô tả tool:

```
Call get_shop_status when the customer asks whether the owner is busy or free.
Call get_shop_hours when they ask what time the salon opens or closes, or
whether it is open on a given day. Both return JSON, never a finished
sentence — write the sentence yourself, and copy clock times from the `spoken`
field exactly as written. An empty `closed_days` means the salon opens every
day. Never invent opening hours.
```

Giữ nguyên mọi luật khác. Nếu `test_the_prompt_actually_got_shorter` đỏ vì trần độ dài, **nới trần và ghi tiếp vào nhật ký đo có sẵn ngay trong test đó** — đừng xoá trần.

- [ ] **Step 5: Chạy cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/prompts.py app/agents/booking_graph/tools.py tests/test_prompts.py tests/test_graph_wiring.py
git commit -m "refactor: mệnh lệnh về prompt, hàng rào chặn tool nhét chỉ dẫn

Chỉ dẫn thuộc system prompt (ổn định, được cache); kết quả tool đổi mỗi
lần gọi. Để cả hai nơi là luật có hai bản rồi trôi lệch.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Chạy thật và ghi tài liệu

**Files:**
- Modify: `CONTEXT.md`, `NOTE.md`
- Chạy: `scripts/llm_scenarios.py`

- [ ] **Step 1: Chạy lại kịch bản thật**

```bash
docker compose up -d mongo
docker compose exec -T mongo mongosh salon_booking --quiet --eval '
  const u = db.users.find({phone: /^0986/}).toArray().map(x => String(x._id));
  db.appointments.deleteMany({user_id:{$in:u}}); db.conversations.deleteMany({user_id:{$in:u}});
  db.users.deleteMany({phone: /^0986/}); db.rate_limits.deleteMany({});'
PYTHONPATH=. .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 &
PYTHONPATH=. .venv/bin/python scripts/llm_scenarios.py LLM-32 > /tmp/llm32-sau-refactor.txt 2>&1
```

Soi bằng mắt, báo lại **từng** điểm:

1. Có chuỗi JSON, khoá (`iso`, `held`, `slots`, `error`) hay chuỗi `HH:MM` nào **rò ra câu nói với khách** không? Đây là rủi ro số một của đợt này.
2. Giờ có còn đọc đúng giọng người Việt ("4 giờ chiều", không phải "16:00") không?
3. Giọng có tự nhiên hơn bản cũ không — hay các câu vẫn na ná nhau? Đối chiếu với `hoi-thoai-dai-sau-khi-fix.txt` đang có ở gốc repo.
4. Xưng hô: "em"/"anh chị", không có "con"/"cô"/"chú"/"bác".
5. Đọc `conversations.state.slots` trong Mongo, xác nhận tầng slots vẫn chạy.

Nếu môi trường không sẵn sàng, ghi rõ **"chưa chạy được"** kèm lỗi thật. **Tuyệt đối không** viết kết quả phỏng đoán. Tắt uvicorn khi xong.

- [ ] **Step 2: Ghi bẫy mới vào CONTEXT.md**

Thêm vào mục các bẫy (tăng số đếm ở tiêu đề), nhóm "Agent và streaming":

```markdown
23. **Tool trả JSON, KHÔNG trả câu.** Trước 2026-09-20, 5/7 tool trả câu tiếng Việt hoàn chỉnh và prompt bảo model chép lại — giọng chết cứng, và tầng `guard`/`rewrite` sinh ra để làm giọng tự nhiên không với tới được vì khuôn câu đã bị tool áp đặt. Tool cũng nhét cả **mệnh lệnh** vào kết quả trả về ("Hãy hỏi khách xác nhận"), làm cùng một luật có hai bản ở `prompts.py` và `tools.py` rồi trôi lệch. Mốc thời gian cấp cả `iso` lẫn `spoken`: bỏ `spoken` đi là bắt model tự định dạng giờ, đúng thứ `guard.check_clock` sinh ra để chặn. `test_tools_return_data_not_instructions` canh.
```

Trong bảng "Chốt cứng", thêm một dòng: **Tool** | **Chỉ cấp dữ kiện JSON, không viết câu** | Câu là việc của LLM; tool viết câu thì guard không sửa được giọng.

- [ ] **Step 3: Cập nhật NOTE.md**

Đổi ngày cập nhật, ghi đợt này đã xong kèm số test mới, và ghi kết quả chạy thật ở Step 1 (đạt hay chưa).

- [ ] **Step 4: Chạy cả bộ lần cuối và commit**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
git add CONTEXT.md NOTE.md
git commit -m "docs: bẫy #23 — tool cấp dữ kiện, không viết sẵn câu

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Không merge, không push.** Ba nhánh đang xếp chồng chưa merge (`feat/conversation-state` → `fix/live-run-findings` → nhánh này); cách gộp là quyết định của người dùng.
