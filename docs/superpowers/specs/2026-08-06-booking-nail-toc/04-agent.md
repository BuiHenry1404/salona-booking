# Agent (LangGraph)

## Đồ thị

```
input → load_context (không LLM)
          nạp song song: hồ sơ user, lịch sử chat theo ngân sách token,
          lịch sắp tới, trạng thái tiệm, pending_confirmation, recall() từ Mem0
        ↓
        route_from_state (không LLM)
        ├→ CÓ pending_confirmation ──→ confirm — "ừ" đi thẳng vào thực thi,
        │                                        BỎ QUA supervisor, tốn 0 lượt LLM
        └→ không có ──────────────────→ supervisor (LLM) — phân loại ý định
                                         ├→ refuse       — ngoài chủ đề, từ chối lịch sự
                                         ├→ StatusAgent  — bận/rảnh, mấy giờ xong
                                         └→ BookingAgent — giờ trống, giữ chỗ, hủy,
                                                           xem lịch của tôi
        ↓
        mỗi nhánh tự sinh `answer` rồi kết thúc — không có node gộp riêng
        ↓
        save_memory (background, sau khi user đã nhận trả lời)
          → remember() qua Mem0
```

Đường đi khách cảm nhận được là **2 lượt LLM** (supervisor + subagent). Lượt xác nhận đi nhánh `confirm` nên tốn **0 lượt**.

## State

`GraphState` là một `TypedDict` chứa `messages`, `user_id`, `context_block`, `recalled`, `pending_confirmation`, `route`, `answer`.

Hồ sơ user, trạng thái tiệm và lịch sắp tới **không** nằm riêng trong state — chúng đã được `load_context` gộp thành `context_block`, một chuỗi dựng bằng code. Giữ chúng thành trường riêng nghĩa là mỗi node phải tự biết cách diễn đạt chúng thành lời, và sớm muộn hai node sẽ diễn đạt khác nhau.

Mọi node đọc và ghi qua state, không có biến toàn cục, nên từng node kiểm thử độc lập được.

## Tool

| Tool | Agent | Việc |
|---|---|---|
| `get_shop_status()` | Status | Đọc `shop_status`, trả mốc giờ xong (không phải "còn N phút") |
| `parse_time(text)` | Booking | Quy "mai 3h chiều" ra ISO, hoặc nói rõ còn thiếu gì — xem [thiết kế riêng](../2026-08-08-vi-time-parser-design.md) |
| `find_free_slots(date)` | Booking | Các mốc 15 phút còn trống, trong `shop_hours`, không ở quá khứ |
| `propose_appointment(start_at, note)` | Booking | Kiểm giờ còn trống, **lưu `pending_confirmation` vào Mongo**, chưa ghi lịch |
| `list_my_appointments()` | Booking | Lịch sắp tới của chính user, kèm ID |
| `cancel_appointment(id)` | Booking | Chỉ hủy được lịch của chính mình |

Khách lớn tuổi không bao giờ biết ID lịch, nên prompt của BookingAgent bắt buộc: muốn hủy thì **luôn gọi `list_my_appointments()` trước** để lấy ID, và nếu khách có từ hai lịch trở lên thì phải hỏi rõ hủy lịch nào trước khi gọi `cancel_appointment`.

Tương tự với thời gian: khách nhắc tới giờ giấc thì **luôn gọi `parse_time()` trước**, không tự tính ngày. Việc quy đổi tách ra một đơn vị riêng để nó test được và có một lớp kiểm tra bằng code đứng sau — kiểu hỏng nguy hiểm nhất không phải lệch một giờ mà là lệch cả năm, và một ngày của năm ngoái vẫn là chuỗi ISO hợp lệ.

## Giới hạn phạm vi "AI chỉ để đặt lịch"

Thực thi bằng ba lớp chồng nhau, không dựa vào riêng prompt:

1. Supervisor có nhánh `refuse` tường minh trong đồ thị. Câu ngoài chủ đề không bao giờ tới subagent.
2. Mỗi subagent chỉ được cấp đúng bộ tool của nó. Không có tool nào ra ngoài phạm vi đặt lịch.
3. `user_id` không lấy từ nội dung tin nhắn mà bơm vào state từ JWT. AI không thể bị dụ thao tác lịch của người khác, kể cả khi user gõ "hủy lịch của bà Lan".

## Memory

Ba tầng tách bạch. Điểm cốt lõi: **"nhớ user là ai" và "nhớ ngữ cảnh cũ" là hai việc khác nhau và phải đi hai đường khác nhau.** Gộp chung là nguồn gốc của lỗi gọi nhầm tên.

| Tầng | Nguồn | Chống được gì | Tính chất |
|---|---|---|---|
| 1. Danh tính | JWT + `users` trong Mongo | Gọi nhầm tên, hỏi lại tên/SĐT | Tất định, luôn đúng |
| 2. Lịch sử hội thoại | `messages` trong Mongo, cắt theo ngân sách token | Lặp trong cùng phiên, hỏi lại thứ vừa nói | Tất định, theo thứ tự |
| 3. Ngữ nghĩa | Mem0 + pgvector | Lặp qua nhiều ngày, mất ngữ cảnh cũ | Xác suất, có thể sai |

**Tên gọi không bao giờ đến từ tầng 3.** Vector search là truy hồi xác suất — nó có thể trả về "cô Lan thích đặt buổi sáng" trong khi người đang đăng nhập là cô Hoa. Nên danh tính đi thẳng từ JWT vào một khối bối cảnh dựng bằng code:

```
Bạn đang nói chuyện với: Nguyễn Thị Lan (0912345678).
Xưng hô: gọi "cô Lan", tự xưng "con".
Lịch sắp tới: Thứ Năm 7/8, 3:00 chiều — làm tóc.
Chủ tiệm: đang bận, xong lúc 3:30 chiều.
```

Khối này không bao giờ do LLM sinh ra. Mem0 chỉ được bổ sung sở thích và ngữ cảnh, không được ghi đè danh tính. Prompt nói rõ: memory mâu thuẫn với khối bối cảnh thì tin khối bối cảnh.

## Bố cục prompt: xếp theo độ ổn định

Khối bối cảnh **không đặt trong system prompt**, dù trực giác đầu tiên là làm vậy. Lý do là prompt caching hoạt động theo **tiền tố chung dài nhất**: khối bối cảnh đổi mỗi lượt (dòng "Bây giờ là…" nhích theo từng phút), nên đặt nó ở đầu sẽ làm mọi lượt chat khác nhau ngay từ token đầu tiên và **cache không bao giờ trúng**. Toàn bộ prompt bị tính giá đầy đủ mỗi lần.

Nguyên tắc: **ổn định nhất đứng trước, hay đổi nhất đứng sau.**

```
[System]   hướng dẫn tĩnh + schema tool     ← không đổi giữa các lượt, được cache
[Messages] lịch sử hội thoại                 ← chỉ thêm vào đuôi, tiền tố vẫn ổn định
[Message]  khối bối cảnh + memory Mem0       ← đổi mỗi lượt, đặt sát cuối
[Message]  tin nhắn mới của khách
```

Lịch sử hội thoại đi vào **mảng `messages` thật**, không nhồi thành văn bản trong system prompt. Khối bối cảnh trở thành một message ngay trước tin nhắn mới nhất — mô hình vẫn đọc được vì nó nằm gần câu hỏi nhất, mà tiền tố phía trên vẫn nguyên vẹn để cache.

**Mỗi lượt gọi chỉ nhận đúng thứ nó cần.** Supervisor chỉ phân loại ý định, nên **không** nhận memory, **không** nhận schema tool, **không** nhận lịch sắp tới — chỉ 4 lượt chat cuối. Riêng việc này cắt lượt supervisor từ ~500 xuống ~200 token.

**Cắt lịch sử theo ngân sách token, không theo số lượt.** Một khách nói dài dòng chiếm gấp nhiều lần một khách nói cộc lốc, nên đếm lượt là sai đơn vị. Lấy ngược từ tin mới nhất cho tới khi chạm trần **1.500 token**, và luôn giữ trọn cặp hỏi–đáp chứ không cắt giữa chừng.

**Đọc.** `load_context` gọi `recall(user_id, câu hỏi hiện tại, limit=5)`, chạy song song với các truy vấn Mongo nên không cộng thêm độ trễ.

**Ghi.** `save_memory` gọi `remember(user_id, lượt vừa rồi)` như background task sau khi user đã nhận câu trả lời. Mem0 tự lo trích xuất, khử trùng lặp và quyết định thêm/cập nhật/xóa, nên lượt LLM nội bộ của nó **không cộng vào thời gian chờ**. Đường đi mà khách cảm nhận vẫn là 2 lượt LLM.

**Adapter async.** Mem0 có thể chỉ có API đồng bộ — tài liệu không xác nhận lớp async. Adapter mặc định chạy Mem0 trong threadpool bằng `asyncio.to_thread`; nếu lúc triển khai xác nhận có API async thì đổi ruột adapter, bên ngoài không bị ảnh hưởng.

**Fail-soft.** `recall` lỗi hoặc quá 2 giây thì trả mảng rỗng, chat chạy bình thường, ghi log cảnh báo. `remember` lỗi thì chỉ ghi log — nó chạy nền, không ai đang chờ.

## Chống lặp hành động

Giải bằng cơ chế tất định, không dựa vào AI:

- `AppointmentService.create` nhận thêm **khóa idempotency** dựng từ `(user_id, start_at)`. Gọi lại hai lần cùng tham số thì lần thứ hai trả về chính lịch đã tạo thay vì tạo mới. Unique index trên `slot_keys` đã chặn ở tầng DB; khóa idempotency giúp trả lời đúng thay vì báo trùng cho chính khách vừa đặt.
- **Agent không có tool nào ghi lịch.** Bộ tool của BookingAgent là `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment`. Lịch chỉ được tạo ở node `confirm`, sau khi khách đồng ý.

  Đây là ràng buộc cấu trúc thay cho lời dặn trong prompt. Quy tắc "luôn nhắc lại ngày giờ cho khách xác nhận rồi mới ghi" nếu chỉ nằm trong prompt thì model bỏ qua lúc nào không biết; bỏ hẳn tool đi thì nó **không có đường nào** ghi thẳng.

- **`propose_appointment` là chỗ cờ `pending_confirmation` được bật.** Nó kiểm giờ còn trống (một lần gọi `find_free_slots` lọc sẵn cả quá khứ, ngoài giờ mở cửa, ngày nghỉ và giờ đã có người), rồi ghi `{start_at, note, asked_at}` vào document `conversations`. Kiểm **trước** khi hỏi khách, vì hỏi "3 giờ chiều đúng không cô?" rồi mới báo giờ đó có người là bắt khách chọn lại hai lần.

  Lượt sau, `load_context` nạp cờ lên, `route_from_state` thấy có cờ thì đi thẳng node `confirm`, bỏ qua supervisor. Câu "ừ", "đúng rồi", "ok" vì thế tốn **0 lượt LLM**. `confirm` xóa cờ rồi gọi service. Cờ hết hạn sau 10 phút — quá đó thì khách nói "ừ" cũng phải hỏi lại, vì nhiều khả năng họ đang nói về chuyện khác.

  **Giá trị đem đi ghi lịch đọc từ Mongo, không phải từ chuỗi model gõ lại.** Đây mới là lý do chính của cả cơ chế. Nếu để model tự chuyển chuỗi ISO từ lượt này sang lượt sau, nó chép sai `15:00` thành `5:00` là không có gì phát hiện được — `create` chỉ thấy một thời điểm hợp lệ. Cho thời gian đi qua model đúng **một** lần (lúc nhắc lại cho khách nghe) rồi lấy giá trị tất định từ DB.

**Về số lần ghi Mongo.** Lượt giữ chỗ và lượt xác nhận mỗi lượt ghi 3 lần lên cùng một document (2 lần `append` tin nhắn + 1 lần đặt/xóa cờ). Đã cân nhắc gộp thành một `update_one` và **quyết định không làm**: với tiệm cỡ 50 lượt chat mỗi ngày thì tổng cộng khoảng 350 lần ghi/ngày trên một document nhỏ — không đáng để đổi lấy việc `propose_appointment` phải ghi vào state qua closure rồi mới persist cuối lượt, vì tool có tác dụng phụ ẩn khó đọc hơn hẳn gọi thẳng `set_pending`. Nếu về sau độ trễ thành vấn đề thật thì chỗ gộp rẻ nhất là hai lời gọi `append` trong `run_turn`.

## Streaming và hiển thị gọi tool

Code hiện tại **chưa có streaming thật**: `soulcare_team.py` phát từng message hoàn chỉnh chứ không phát token, và `ToolCallRequestEvent` chỉ được `print()` ra console nên giao diện không bao giờ biết agent đang làm gì. Phần này là làm mới.

Nguồn sự kiện là `graph.astream_events(...)` của LangGraph.

| Sự kiện | Payload | Gửi tới | Khi nào |
|---|---|---|---|
| `turn_started` | — | phòng của user | Nhận tin nhắn, trước lượt LLM đầu |
| `tool_started` | `{name}` | phòng của user | `on_tool_start` |
| `tool_finished` | `{name, ok}` | phòng của user | `on_tool_end` hoặc lỗi |
| `token` | `{text}` | phòng của user | `on_chat_model_stream` |
| `complete` / `error` | `{message_id}` | phòng của user | Đồ thị chạy xong hoặc lỗi |
| `appointment_created` | `{appointment}` | phòng của admin | Đẩy lịch mới lên đầu màn hình lịch hôm nay |
| `shop_status_changed` | `{is_busy, busy_until}` | **broadcast tới mọi user đang online** | Cập nhật thẻ trạng thái tiệm mà không cần tải lại |

**Chỉ stream token mang tag `respond`.** Tag đó gắn cho model của hai subagent (Status và Booking) — nơi sinh câu trả lời cuối cùng cho khách. Supervisor mang tag `supervisor`, parser thời gian mang `timeparse`; token của chúng là JSON, phát ra màn hình sẽ thành rác chạy ngang. Bộ dịch sự kiện chỉ chuyển tiếp `on_chat_model_stream` mang tag `respond`.

**Tên tool không bao giờ tới mắt khách.** Backend gửi tên tool thô (`find_free_slots`); frontend ánh xạ sang câu tiếng Việt. Bảng ánh xạ nằm ở frontend để đổi câu chữ không phải deploy lại API — xem [05-frontend.md](05-frontend.md).

`shop_status_changed` là thứ làm cho thẻ trạng thái ở [màn hình chat](05-frontend.md) thật sự realtime. Phát ra khi admin bấm bận/rảnh — **từ web app hay từ bot Telegram đều phải phát**.

**Lúc `busy_until` hết hạn thì KHÔNG phát sự kiện nào.** Backend không có timer; hết hạn chỉ được tính lại khi có ai gọi `get_status()`. Frontend tự đếm ngược và tự đổi thẻ — xem [05-frontend.md](05-frontend.md). Đây là lựa chọn có chủ ý: một timer phía server sẽ chết khi restart, phát trùng khi chạy nhiều worker, và phải nhớ hủy mỗi khi trạng thái đổi — ba chỗ hỏng mới để đổi lấy đúng thứ mà client cho không.

Socket.IO và Telegram là hai kênh của cùng một việc "báo cho chủ tiệm", nên cả hai đi qua một chỗ tỏa tin duy nhất là `app/services/notifications.py` (xem [06-telegram.md](06-telegram.md)).

## Quan sát bằng Langfuse

Gắn Langfuse callback handler vào LangGraph để mỗi lượt chat sinh ra một trace lồng nhau: node, lượt gọi LLM, tool call, độ trễ, token, chi phí. Trace mang `user_id` và `conversation_id` để lần ngược từ một lịch sai về đúng cuộc hội thoại sinh ra nó.

Lưu ý: Mem0 dùng LLM client riêng, **không đi qua LangChain**, nên callback handler của LangGraph không tự thấy lượt gọi trích xuất của nó. Muốn đo chi phí thật của memory thì adapter phải tự bọc `remember()` trong một span Langfuse tường minh. Nếu không làm, chi phí memory sẽ vô hình trong trace — chấp nhận được ở giai đoạn đầu, nhưng phải biết là mình đang không nhìn thấy nó.

Ba thứ cần theo dõi thường xuyên: tỷ lệ đi vào nhánh `refuse` (cao bất thường nghĩa là supervisor chặn nhầm câu hợp lệ), độ trễ đầu-cuối (ngưỡng chú ý: 5 giây), và tỷ lệ lỗi tool.

Langfuse chỉ được bật khi có key trong env. Thiếu key thì app chạy bình thường không trace, và Langfuse lỗi không bao giờ làm hỏng một lượt chat.

## Chi phí và tốc độ

Đường đi thường gặp là 2 lượt LLM (supervisor + subagent), cộng 1 lượt nền do Mem0 gọi khi trích xuất. Với người lớn tuổi đang chờ trả lời, đây là con số cần giữ.

Ngân sách token input sau khi áp bố cục ở trên:

| Lượt gọi | Được cache | Không cache |
|---|---|---|
| supervisor | ~300 (hướng dẫn) | ~200 (4 lượt chat cuối) |
| subagent | ~700 (hướng dẫn + schema tool) | ~900 (lịch sử + bối cảnh + memory) |

Lượt xác nhận ("ừ" → thực thi) tốn **0 lượt LLM** nhờ nhánh tắt `pending_confirmation`.

Với một tiệm cỡ 50 lượt chat mỗi ngày, chi phí ở mức không đáng kể — lý do tối ưu bố cục prompt là để cache trúng và độ trễ thấp, không phải để tiết kiệm tiền. Nếu về sau vẫn chậm, cách tối ưu tiếp theo là gộp supervisor và subagent thành một lượt tool-calling trực tiếp.

