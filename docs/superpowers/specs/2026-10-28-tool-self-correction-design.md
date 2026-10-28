# Cơ Chế Tự Sửa Lỗi Khi Gọi Tool (Tool Self-Correction Loop) Cho Agent — Thiết Kế

Ngày: 2026-10-28.

## 1. Bối cảnh & Vấn đề (Motivation)

Trong hệ thống đặt lịch salon hiện tại (`app/agents/booking_graph/`), các Agent (`booking`, `shop`) tương tác với hệ thống và cơ sở dữ liệu MongoDB thông qua các LangChain tools (`find_free_slots`, `propose_appointment`, `parse_time`, `list_my_appointments`, `cancel_appointment`, `get_shop_status`, `get_shop_hours`).

Qua rà soát mã nguồn, hệ thống đang gặp phải 2 điểm nghẽn lớn khiến Agent **không thể tự phục hồi (self-correct)** khi xảy ra lỗi gọi tool:

### 1.1. `ToolFailureMiddleware` che giấu hoàn toàn chi tiết lỗi kỹ thuật
- Khi Agent gọi tool sai cú pháp, thiếu tham số bắt buộc hoặc sai kiểu dữ liệu (gây ra `ToolInvocationError` hoặc `pydantic.ValidationError`), LangChain sinh ra thông báo lỗi chi tiết (vd: `"Field required: 'day'"`).
- Tuy nhiên, tại [`ToolFailureMiddleware`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/middleware/tools.py#L97-L101), mọi lỗi ném ra hoặc mọi `ToolMessage(status="error")` đều bị ghi đè thành một chuỗi tiếng Việt cố định:
  ```python
  TOOL_ERROR_TEXT = "Tra cứu không được, thử lại giúp em ạ."
  ```
- **Hậu quả:** LLM nhận được một câu thông báo mơ hồ như người dùng cuối. LLM không thể biết mình đã truyền sai trường nào hay vi phạm schema gì, dẫn đến việc LLM bỏ cuộc ngay lập tức hoặc bịa câu xin lỗi gửi cho khách hàng, dù trong ngân sách vòng lặp ([`ToolRoundLimitMiddleware`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/middleware/tools.py#L48-L70)) vẫn còn tới 3–4 lượt gọi model.

### 1.2. Các mã lỗi định dạng trả về từ Tool hướng dẫn LLM "hỏi khách ngay"
- Khi Agent truyền sai định dạng ngày hoặc giờ vào các tool (vd: `find_free_slots(day="hôm nay")` thay vì ISO date `YYYY-MM-DD`, hoặc `propose_appointment(start_at="2026-10-28")` thiếu giờ cụ thể):
  - Tool trả về JSON: `{"error": "bad_day_format"}` hoặc `{"error": "missing_time"}`.
  - Trong [`BOOKING_PROMPT`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/prompts.py#L184-L185), quy tắc hiện tại ghi:
    > `- bad_time_format / bad_day_format: the date or time could not be read; ask them to say it again.`
- **Hậu quả:** LLM vội vã quay sang hỏi lại khách hàng (vd: *"Anh chị cho em xin lại ngày ạ"*), trong khi ngày hôm nay đã có sẵn ngay dòng đầu tiên của `context_block` (`"Bây giờ là 14:00 Thứ Tư, 2026-10-28"`), hoặc giờ có thể lấy được nếu LLM gọi tool `parse_time` trước.

### 1.3. Mục tiêu thiết kế
Xây dựng cơ chế **Tool Self-Correction Loop** đáp ứng 4 tiêu chuẩn:
1. **Phân loại lỗi chuẩn xác trong Middleware:** Phân biệt rõ giữa **lỗi schema/tham số do LLM tạo ra** (cần feedback kỹ thuật chi tiết để LLM tự sửa) và **lỗi hạ tầng hệ thống/DB** (cần giữ `TOOL_ERROR_TEXT` để fail-safe).
2. **Cung cấp `hint` sửa lỗi từ Tool JSON:** Bổ sung hướng dẫn khắc phục cụ thể trong payload trả về của tool khi gặp lỗi định dạng (`bad_day_format`, `bad_time_format`, `missing_time`).
3. **Quy tắc Self-Correction trong System Prompt:** Dạy cho LLM biết tận dụng `ToolMessage` lỗi và `hint` để tự điều chỉnh tham số, tra lại context và gọi lại tool ngay trong lượt chat hiện tại.
4. **Bảo mật & An toàn tuyệt đối:**
   - Chi tiết kỹ thuật của tool nằm hoàn toàn trong `ToolMessage`, **không bao giờ stream ra giao diện khách hàng** (nhờ cơ chế lọc tag `respond` của `events.py`).
   - Giới hạn trần vòng lặp [`ToolRoundLimitMiddleware`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/middleware/tools.py#L28) (`MAX_TOOL_ROUNDS = 4`) ngăn chặn 100% nguy cơ lặp vô hạn nếu model không sửa được lỗi.

---

## 2. Kiến trúc & Thiết kế Chi Tiết

```mermaid
flowchart TD
    LLM["1. Agent Model (LLM) phát sinh Tool Call"] --> TC{"2. Thực thi Tool qua ToolFailureMiddleware"}
    
    TC -->|Thành công| OK["Tool trả về kết quả JSON hợp lệ"]
    OK --> AG["Agent tiếp tục suy luận / phản hồi khách"]

    TC -->|Lỗi Schema / Validation\n(ToolInvocationError / ValidationError)| VAL["3a. Phân loại: Lỗi tham số do LLM\nTrả về ToolMessage mang chi tiết schema & chỉ dẫn sửa"]
    TC -->|Lỗi Hạ tầng\n(PyMongoError / Mongo timeout / ServerError)| INF["3b. Phân loại: Lỗi hạ tầng\nTrả về ToolMessage(TOOL_ERROR_TEXT) fail-safe"]
    
    OK -.->|Nếu JSON trả về mã lỗi format\nbad_day_format / bad_time_format| HINT["3c. Kèm theo field 'hint' hướng dẫn sửa"]
    
    VAL --> LOOP{"4. Kiểm tra ToolRoundLimitMiddleware\n(Số round < 4?)"}
    HINT --> LOOP
    INF --> LOOP

    LOOP -->|Còn lượt (< 4)| RETRY["5. LLM đọc ToolMessage lỗi + hint\nTự điều chỉnh tham số & gọi lại tool"]
    RETRY --> LLM

    LOOP -->|Hết lượt (>= 4)| FALLBACK["Dừng vòng lặp -> Trả câu cứu cánh cố định\nFALLBACK_ANSWER"]
```

### 2.1. Nâng cấp `ToolFailureMiddleware` ([`app/agents/booking_graph/middleware/tools.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/middleware/tools.py))

#### Phân loại ngoại lệ (Exception Classification):
Khi bọc quanh `handler(request)`:
1. **Lỗi Schema / Tham số:**
   - Nhận diện qua:
     + `result.status == "error"` từ LangChain `ToolNode` (thường bọc `ToolInvocationError` hoặc lỗi validate Pydantic).
     + Ngoại lệ ném trực tiếp: `pydantic.ValidationError`, `TypeError` (thiếu positional argument), `ValueError` về kiểu tham số.
   - Xử lý: Trích xuất nội dung lỗi kỹ thuật ngắn gọn, trả về `ToolMessage`:
     ```python
     content = (
         f"Tool '{call['name']}' call failed due to invalid arguments: {error_detail}. "
         "Please inspect the tool schema, correct the arguments, and call the tool again."
     )
     return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"], status="error")
     ```
2. **Lỗi Không Tồn Tại Tool:**
   - Giữ nguyên cơ chế hiện tại: `Không có tool tên {call['name']}.` kèm danh sách tool hợp lệ nếu cần.
3. **Lỗi Hạ Tầng & Hệ Thống Tạm Thời:**
   - Các ngoại lệ liên quan tới cơ sở dữ liệu (`PyMongoError`, `ServerSelectionTimeoutError`, `NetworkTimeout`), hoặc `RuntimeError`.
   - Xử lý: Giữ nguyên cơ chế an toàn: trả về `TOOL_ERROR_TEXT` (`"Tra cứu không được, thử lại giúp em ạ."`). LLM không thể tự sửa lỗi hạ tầng nên không cung cấp stack trace.

### 2.2. Bổ sung `hint` tự sửa vào các Tool ([`app/agents/booking_graph/tools.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/tools.py))

Khi dữ liệu đầu vào không đúng format mà tool mong đợi, tool trả về mã lỗi kèm `hint` định hướng hành động cho LLM:

1. **Tool `find_free_slots`:**
   ```python
   try:
       parsed_day = date.fromisoformat(day)
   except ValueError:
       return json.dumps({
           "error": "bad_day_format",
           "received": day,
           "hint": "Parameter 'day' must be strictly in YYYY-MM-DD format. Look up today's date in the context block line 'Bây giờ là...' or resolve relative day expressions via parse_time."
       }, ensure_ascii=False)
   ```

2. **Tool `propose_appointment`:**
   - Lỗi `missing_time`:
     ```python
     if _is_date_only(start_at):
         return json.dumps({
             "error": "missing_time",
             "received": start_at,
             "hint": "Parameter 'start_at' must be a full ISO 8601 string including hours and minutes (e.g., 2026-10-28T14:00:00+07:00). If you only have a date, call parse_time first or ask the customer for the specific hour."
         }, ensure_ascii=False)
     ```
   - Lỗi `bad_time_format`:
     ```python
     try:
         start = _parse_local(start_at)
     except ValueError:
         return json.dumps({
             "error": "bad_time_format",
             "received": start_at,
             "hint": "Parameter 'start_at' could not be parsed into datetime. Pass the exact 'start_at' ISO string returned by parse_time."
         }, ensure_ascii=False)
     ```

### 2.3. Cập nhật Quy Tắc Self-Correction trong `BOOKING_PROMPT` ([`app/agents/booking_graph/prompts.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/agents/booking_graph/prompts.py))

Cập nhật mục quy tắc xử lý lỗi trong prompt:

```text
2. Every tool returns JSON, never a finished sentence...
   `error` codes and self-correction rules:
   - When a tool returns an error with a `hint` field, or returns an argument schema error:
     DO NOT immediately ask the customer to repeat themselves if the information is already
     available in the context block (e.g. today's date from the 'Bây giờ là...' line) or if
     the format can be corrected by calling parse_time first.
     Self-correct the parameters and invoke the tool again within the same turn.
   - Only when the missing information was genuinely never provided by the customer
     (e.g., they named a day but completely omitted the hour), write a concise question
     asking them for that missing detail.
   - already_yours: they ALREADY have an appointment at that exact time...
   - slot_unavailable: someone already booked `asked`; offer `nearest`...
   - outside_hours / closed_day / time_passed: the salon cannot take `asked`...
```

---

## 3. Ranh Giới An Toàn & Bảo Mật (Safety & Guards)

1. **Bảo vệ rò rỉ token kỹ thuật (No Technical Leak to Customer):**
   - Trong `app/agents/booking_graph/events.py`, hệ thống chỉ phát sự kiện `token` stream ra màn hình cho các `AIMessageChunk` mang tag `"respond"` (thuộc câu trả lời tự nhiên cuối cùng).
   - Các `ToolMessage` và các lần gọi tool trung gian mang lỗi schema hoàn toàn vô hình đối với người dùng cuối.
2. **Ngăn chặn vòng lặp vô hạn (Infinite Loop Prevention):**
   - `ToolRoundLimitMiddleware` hiện tại đã đặt trần `MAX_TOOL_ROUNDS = 4`.
   - Nếu LLM cố gắng tự sửa tham số nhưng sau 4 lần gọi model vẫn thất bại, middleware sẽ tự động nhảy (`jump_to="end"`) và phát câu cứu cánh cố định:
     `FALLBACK_ANSWER = "Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm giúp em nhé ạ."`
3. **Tuân thủ nguyên tắc ghi chốt lịch (Write Fence):**
   - Cơ chế self-correction chỉ áp dụng cho việc gọi tool hợp lệ. Không có bất kỳ tool nào được cấp quyền ghi trực tiếp vào cơ sở dữ liệu cuộc hẹn (vẫn giữ nguyên kiến trúc Proposal qua `propose_appointment`).

---

## 4. Kế Hoạch Kiểm Thử (Test Plan)

### 4.1. Unit Tests (`tests/test_tool_middleware.py`)
- `test_tool_invocation_error_returns_schema_feedback`: Giả lập gọi tool với thiếu tham số bắt buộc. Xác minh `ToolMessage` chứa chuỗi báo lỗi schema chi tiết và hướng dẫn sửa, không phải `TOOL_ERROR_TEXT`.
- `test_infrastructure_error_returns_vietnamese_fallback`: Giả lập tool ném `PyMongoError` hoặc `ServerSelectionTimeoutError`. Xác minh `ToolMessage` vẫn trả về `TOOL_ERROR_TEXT` nguyên vẹn.
- `test_tool_round_limit_still_enforced`: Giả lập tool liên tục trả lỗi schema. Xác minh sau 4 rounds, agent dừng và trả `FALLBACK_ANSWER`.

### 4.2. Tool Output Tests (`tests/test_tools.py`)
- `test_find_free_slots_bad_day_returns_hint`: Gọi `find_free_slots("mai")` -> kiểm tra JSON có `"error": "bad_day_format"` và trường `"hint"` chứa hướng dẫn `YYYY-MM-DD`.
- `test_propose_appointment_missing_time_returns_hint`: Gọi `propose_appointment("2026-10-28")` -> kiểm tra JSON có `"error": "missing_time"` và trường `"hint"` hướng dẫn dùng ISO 8601 từ `parse_time`.

### 4.3. Integration & Self-Correction Agent Tests (`tests/test_agent_self_correction.py`)
- `test_agent_self_corrects_bad_day_format`: Khách nói *"Tìm giờ trống hôm nay"*, giả lập Agent ban đầu gọi `find_free_slots(day="hôm nay")`. Nhận được `hint`, Agent tự lấy ngày hôm nay từ context block (`2026-10-28`), gọi lại tool thành công và trả lời khách với danh sách giờ trống mà không cần hỏi lại khách.
- `test_regression_existing_tests`: Toàn bộ 281+ tests hiện tại tiếp tục pass 100%.
