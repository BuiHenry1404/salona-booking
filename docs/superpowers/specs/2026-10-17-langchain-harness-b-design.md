# Harness B: chuyển agent sang khung LangChain — thiết kế

Ngày: 2026-10-17. Nhánh: `feat/langchain-harness` (tách từ `docs/arch-security-review`
@ `9f7fb44`, đúng mã nguồn đã đo baseline A).

## Vì sao

Harness hiện tại (A) là LangGraph tự xây: supervisor → booking/shop/social, mỗi
subagent là một vòng lặp tool viết tay (`make_subagent_node`), bước chốt lịch là
node `confirm` đọc câu khách bằng regex `is_affirmative`, lịch chờ chốt nằm trong
Mongo (`pending_confirmation`). Baseline A (17/10/2026, k=4) cho thấy:

- Nhóm edge chỉ đạt 28,6%. G-01/G-02/G-03 và `is-affirmative-tan-gau` ghi sai 100%:
  khách từ chối ("dạ ko em", "để chị hỏi thằng Huy đã") mà vẫn bị ghi/huỷ lịch.
- LLM-22: đổi giờ không kèm ngày thì 2/4 lượt ghi nhầm sang hôm nay.
- (Điểm judge của baseline không dùng được: benchmark tự gắn tên tool vào transcript
  gửi judge, bot thật không lộ tên tool — 0/499 câu. Benchmark sửa lỗi này rồi chấm lại.)

Người dùng quyết bám docs LangChain cho cả dự án (`create_agent` + middleware +
`interrupt` + checkpointer Mongo), chấp nhận cài thêm thư viện, và giao cho
Claude quyết các chi tiết. Benchmark ở nhánh `bench/harness-benchmark` là thước đo.

## Người dùng đã quyết

- Theo framework LangChain, không tự xây vòng lặp.
- **Agent vẫn không có tool ghi.** Ghi lịch do code gọi `AppointmentService` bằng
  số liệu trong state, không bao giờ từ chuỗi model gõ. Không dùng
  `HumanInTheLoopMiddleware` gác tool ghi.
- Chốt lịch bằng middleware tự viết dùng `interrupt()`.
- **Chưa làm nút bấm.** Khách vẫn gõ chữ để chốt; giao thức Socket.IO giữ nguyên.
- Một dự án, một nhánh; mỗi giai đoạn là một mốc commit; đo benchmark sau mốc.
- Gọi Azure thật, không giới hạn chi phí.

## Mục tiêu

So với A trên cùng bộ benchmark (k=4, cùng model, cùng ngày chạy):

- Ghi sai ở G-01, G-02, G-03, `is-affirmative-tan-gau` về **0**.
- Nhóm edge đạt cao hơn 28,6%; nhóm llm không thấp hơn A quá nhiễu (xem "Cổng đo").
- Fault vẫn 12/12, không lộ lỗi kỹ thuật.
- Điểm judge (sau khi benchmark sửa) không thấp hơn A.
- Code agent đọc được bằng docs LangChain: mỗi luật một middleware, test riêng được.

## Không làm

- Nút bấm / thẻ xác nhận trên web hay Telegram, `AdapterB`.
- Đổi regex đọc giờ (G-05, G-06): vẫn là code của mình.
- Đổi REST API, màn lịch sử chat, luồng admin.
- Nhiều worker, load test.

## Phản mục tiêu

- Thêm tool ghi cho agent, dù gác bằng HITL.
- Câu khách mơ hồ bị hiểu là đồng ý. Sai hướng "hỏi lại thêm một câu" chấp nhận
  được; sai hướng "ghi lịch khi khách chưa đồng ý" thì không.
- Sửa kịch bản benchmark cho B qua. Kỳ vọng kịch bản không đổi theo output
  (ngoại lệ duy nhất: kịch bản có kỳ vọng bất khả thi như LLM-11, sửa trước khi
  đo B và đo lại cả A).
- Kết luận từ một lần chạy, hoặc so B với số liệu A cũ thay vì chạy lại A cùng lúc.

## Ràng buộc (giữ từ AGENTS.md / CONTEXT.md)

- Đúng 1 worker. Config chỉ từ `.env`; thêm setting = `config.py` + `.env.example` + `.env`.
- Mọi logic đặt lịch ở `app/services/`; middleware/tool chỉ là lớp mỏng.
- Chỉ stream token mang tag `respond`; supervisor, bộ phân loại câu trả lời,
  time-parser không bao giờ ra màn hình.
- Khối bối cảnh có ngày hôm nay, mỗi lượt một bản, không nằm trong system prompt cache.
- Index unique partial của slot, hủy = đổi `status`.
- 21+ bẫy đã sửa trong `CONTEXT.md`: không "dọn" chúng. Bẫy nào không còn áp dụng
  vì kiến trúc đổi thì ghi rõ trong `CONTEXT.md` lý do.
- Giao thức Socket.IO (sự kiện `turn_started` / `token` / `tool_*` / `complete` /
  `error`) giữ nguyên, để benchmark dùng lại `AdapterA`.

## Thiết kế theo mốc

Mỗi mốc: app chạy được, `pytest` xanh, commit có tag `harness-b/m<N>`.

### M0: nền

- Cài `langgraph-checkpoint-mongodb` (kéo pymongo 4.6 → ~4.19). Kiểm motor 3.3.2
  chạy với pymongo mới; nếu không, nâng motor tối thiểu cần thiết. Pin chính xác
  mọi gói `langchain*`, `langgraph*`, `pymongo`, `motor` trong `requirements.txt`.
- Toàn bộ test cũ xanh. Không đổi hành vi.

### M1: đổi vòng lặp, giữ nguyên hành vi

- Thay `make_subagent_node` bằng `create_agent` cho booking/shop/social. Graph
  ngoài (supervisor, confirm, guard, rewrite, phrase) giữ nguyên.
- Thứ tự message model nhận giữ y như A (system → khối summary/slots → lịch sử →
  khối bối cảnh → câu khách), làm bằng middleware `wrap_model_call` / `before_model`.
- Giới hạn 4 vòng tool, câu fallback "Dạ em chưa tra được…" giữ nguyên; tool ném
  lỗi → câu "Tra cứu không được…" như A (middleware `wrap_tool_call`). Tắt
  `parallel_tool_calls`. Không trạng thái lượt chat trong `self.*` của middleware.
- Tag `respond` vẫn gắn đúng chỗ để `events.py` lọc token.
- **Cổng đo M1:** chạy A và B-M1 cạnh nhau. B-M1 phải "không tệ đi" theo định
  nghĩa ở dưới; khác biệt hành vi ở kịch bản scripted phải giải thích được.

### M2: lịch sử sang checkpointer

- `AsyncMongoDBSaver` (pymongo async) làm checkpointer. `thread_id` =
  `<user_id>:<ngày VN>`: sang ngày mới là thread mới, khớp cách A cắt lịch sử theo
  ngày. TTL cho checkpoint (mặc định 7 ngày, là setting).
- Lịch sử model đọc lấy từ thread, không dựng lại từ `conversations` mỗi lượt.
  Collection `conversations` vẫn được ghi như cũ làm nhật ký hiển thị (REST lịch sử
  không đổi).
- Nén `ConversationState` (summary + slots, bẫy #20/#22) giữ nguyên dịch vụ và
  ngưỡng; middleware `before_model` chỉ gửi model phần tin sau mốc nén cộng khối
  summary/slots — hành vi model thấy như A.
- `pending_confirmation` vẫn theo cơ chế cũ ở mốc này.
- **Cổng đo M2:** như M1.

### M3: chốt lịch bằng `interrupt()` (mốc sửa lỗi chính)

- Bỏ node `confirm` regex và `pending_confirmation` trong Mongo. Thay bằng
  `ConfirmMiddleware`:
  - `propose_appointment` / `cancel_appointment` (vẫn chỉ đọc + giữ chỗ như A) ghi
    đề xuất vào state (`proposal`: `id`, `kind` book/move/cancel, `start_at`,
    `note`, `appointment_id`, `created_at`). Đề xuất lấy từ kết quả service, không
    từ chuỗi model.
  - Sau khi agent đã viết câu hỏi xác nhận (câu này stream ra như thường), nếu state
    có `proposal` thì `interrupt(proposal)`. Lượt chat kết thúc với `complete` như A.
  - Lượt sau, `run_turn` thấy thread đang chờ thì resume bằng
    `Command(resume=<câu khách>)`.
- **Bộ phân loại câu trả lời** (thay cho nút bấm), chạy trong middleware khi resume:
  - Model nhỏ, structured output `{decision: approve|reject|change|unclear}`,
    `tags=["classify"]`, `streaming=False`. Nhận đề xuất, câu bot vừa hỏi, câu khách.
  - Lưới code chạy trước và có quyền phủ quyết: câu có từ phủ định/hoãn (có dấu và
    không dấu, gồm "ko/k/hông/hổng/khoan/chưa/để … hỏi/xem lại"), câu hỏi ngược
    ("…nào vậy?", dấu `?`), hoặc nhắc mốc giờ/ngày khác đề xuất (dùng `timeparse`)
    → **không** approve.
  - Chỉ ghi khi model nói `approve` **và** lưới code không phủ quyết. Lỗi/timeout của
    bộ phân loại = `unclear`.
  - `approve` → code gọi `AppointmentService.create/reschedule/cancel` với số liệu
    trong `proposal`; câu chốt đi qua `phrase` + `guard` như A.
  - Khác `approve` → bỏ đề xuất, câu khách đi tiếp vào agent booking như một tin
    mới (giống A: chưa chốt thì vẫn đang đặt lịch).
  - Đề xuất quá 10 phút (như A) → bỏ, không ghi, câu khách xử lý như tin mới.
- Cập nhật `AGENTS.md`, `CONTEXT.md`: "confirm graph node" → "ConfirmMiddleware +
  interrupt", vẫn không tool ghi. Test fence "không tool ghi" giữ.
- **Cổng đo M3:** mục tiêu ghi sai = 0 ở G-01/02/03/tán gẫu, edge tăng, llm không giảm.

### M4: guard và giới hạn thành middleware

- `guard` / `rewrite` / `phrase` thành middleware `after_agent` (thứ tự và luật giữ
  nguyên). Thêm luật guard chặn câu lộ tên tool (phòng ngừa; A hiện không lộ).
- Retry model có giới hạn và timeout LLM (sửa A-04), câu báo lỗi tiếng Việt như A.
- **Cổng đo M4:** như M1, cộng vi phạm luật `tool_name_leak` = 0 (A cũng phải 0).

### M5: định tuyến

- Thử biến thể **một agent** (một `create_agent` đủ tool, prompt chọn theo ý định
  bằng middleware) so với router hiện tại. Đo cả hai biến thể với A; giữ biến thể
  tốt hơn theo thứ tự ưu tiên: ghi sai → tỉ lệ đạt edge → llm → độ trễ. Hoà thì giữ
  router (ít thay đổi hơn).

## Benchmark

Làm trên `bench/harness-benchmark` trước khi đo B:

1. Hoàn tất review cuối nhánh benchmark (đang dở) và sửa các lỗi nó tìm ra.
2. Sửa kỳ vọng bất khả thi của `LLM-11`.
3. Thêm luật văn bản `tool_name_leak` (áp cho mọi ref, kể cả A).
4. Sửa các lỗi chấm điểm review cuối tìm ra (transcript gửi judge gắn tên tool; ghi sai
   đếm cả lần không ghi; `behavior_match` dùng `before` của ref gốc; gộp run theo tên ref).
   Chấm lại baseline A từ dữ liệu đã lưu.
5. Checkpointer của B phải nằm trong `MONGO_DB_NAME` của ref (không dùng DB mặc định
   `checkpointing_db`), để benchmark cô lập và dọn được.

Lịch đo (mỗi lần đều chạy lại A cạnh B, `--refs a,b` xen kẽ):

| mốc | kịch bản | k |
|---|---|---|
| M1, M2 | mọi tag trừ `slow` | 2 |
| M3, M4 | mọi tag trừ `slow` | 4 |
| M5 (cả hai biến thể) | mọi tag, kể cả `slow` | 4 |

"Không tệ đi": tổng ghi sai của B ≤ A; không kịch bản nào của B có ghi sai mà A
không có; tỉ lệ đạt nhóm llm của B ≥ A − 5 điểm phần trăm; fault đủ. Vi phạm cổng
ở M1/M2 → sửa trước khi sang mốc sau. Vi phạm ở M3+ → điều tra, sửa, đo lại; nếu
không sửa được thì ghi rõ trong báo cáo cuối.

Đo chạy nền trong khi làm mốc tiếp theo được, vì mỗi ref có worktree, DB và cổng riêng.

## Đầu ra

- Nhánh `feat/langchain-harness` với tag `harness-b/m0` … `m5`.
- Báo cáo HTML từ `bench compare` cho mỗi mốc, và một artifact tổng hợp A so với B
  cuối cùng (số liệu + hội thoại edge case song song).
- Không merge, không push khi người dùng chưa yêu cầu.

## Để builder quyết

Tên file/middleware, cấu trúc thư mục bên trong `app/agents/`, prompt của bộ phân
loại, cách test từng middleware, cách migrate test cũ của `confirm.py`.
