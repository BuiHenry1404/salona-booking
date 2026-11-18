# Sửa lỗi của lần chạy thật 2 (A, C, D, E, F)

Ngày 11/11/2026. Chủ dự án duyệt A, C, D, E trong phiên này. Sau khi model Fable review plan,
chủ dự án duyệt thêm hai điều:
- **phương án 1**: giờ khách hỏi bị kín mà không có đề xuất thì luôn là `need_info`, kể cả khi
  khách chỉ hỏi;
- **mục F**: phát hiện giờ kín thì phải báo cho khách biết, có code bảo đảm.

Mục B (luật guard `missing_option`) **bỏ**, vì mức C (câu trả lời tham chiếu dữ kiện) sẽ làm
tốt hơn và có spec riêng sau.

## Bằng chứng

Log: `.superpowers/sdd/2026-11-08-subagents-rebuild/live-2.log` (git-ignored). Kết quả chấm
nằm trong sổ `progress.md` (dòng "live run 2").

**Lượt 2.** Khách hỏi đặt sơn gel 3 giờ chiều mai, giờ này đã có người.
- `propose_appointment` từ chối (`slot_unavailable`) và trả giờ trống 13:00, 14:00, 16:00.
- Agent Đặt lịch vẫn kết thúc bằng `status: "ok"`. Hệ quả: không có đề xuất, không mở phiên
  chờ.
- Orchestrator trả lời *"em đã tìm được giờ Thứ Năm 12/11, 3 giờ chiều cho sơn gel rồi ạ.
  Chị đồng ý giờ này nhé?"*.
- Guard cho qua: 3 giờ có trong dữ kiện; "tìm được" không phải động từ nhận vơ; không có đề
  xuất nên không bắt câu hỏi.

**Nguyên nhân**, đọc từ checkpoint và prompt:
1. `subagents/appointment/prompt.py` định nghĩa *"ok: the task is done (a proposal was
   created, or the information asked for is in the tool results)"*. Giờ kín kèm giờ trống
   khớp vế sau, nên model chọn `ok`. Lần chạy 1, cùng tình huống, model chọn `need_info`.
2. `dispatch.py::run_subagent` tin luôn `SubagentResult.status`, không đối chiếu với dữ kiện
   code đã trích (bẫy 16 của CONTEXT.md).
3. `orchestrator/prompt.py` không nói dữ kiện `taken` nghĩa là gì, và không có luật cho "ok
   mà không có đề xuất". Model mượn luật "đề xuất mới thì hỏi khách đồng ý".
4. Không lớp nào bắt câu trả lời phải báo giờ kín. Lần chạy 1, lượt 4: 4 giờ kín, bot đưa giờ
   khác mà không nói 4 giờ đã có người. Câu mẫu dự phòng chỉ hỏi "muốn mấy giờ ạ?".

**Markdown.** 8/10 câu trả lời ở lần 2 có `**...**` (lần 1 không có câu nào). Frontend hiện
chữ thô, nên khách thấy dấu sao. Prompt chưa có luật định dạng.

## Quyết định

### A — Code sửa trạng thái agent Đặt lịch (`dispatch.py`, `appointment/agent.py`)
- `SubagentSpec` có thêm bước tuỳ chọn `reconcile(result, extracted) -> SubagentResult`, chạy
  ngay sau `extract`, trước khi ghi phiên chờ và dựng ToolMessage.
- Agent Đặt lịch: `status == "ok"` + `not extracted.proposal_created` + trong `extracted.facts`
  của **lần gọi này** có `TimeFact(role="taken")` → đổi thành `need_info`, kèm:
  - `question_field="time"`;
  - `question_text` do code viết (mục F1).
- Lựa chọn của phiên chờ lấy từ `extracted.options` như mọi `need_info` khác.
- **Phương án 1:** dữ kiện `taken` đến từ `propose_appointment` (`slot_unavailable`) hay từ
  `find_free_slots` (`asked_status == "taken"`, khách chỉ hỏi giờ đó còn trống không) đều kích
  hoạt. Khách chỉ hỏi cũng được đưa giờ trống và mở phiên chờ, để lượt sau "4 giờ đi" là đặt
  được.
- Không đụng `need_info`, `failed`, và `ok` có đề xuất (ví dụ hai giờ một tin: 3 giờ kín,
  4 giờ được đề xuất).
- Mỗi lần đổi, ghi log `subagent_status_corrected` với `from` / `to`.
- Agent Dịch vụ và Thông tin tiệm không có bước này.

### C — Không Markdown (`orchestrator/prompt.py`, `guard/checks.py`)
- Prompt Orchestrator, mục "Writing the reply", thêm luật định dạng tiếng Anh: chỉ chữ thường,
  không Markdown (không in đậm, tiêu đề, danh sách).
- `strip_tool_text` xoá thêm mọi `**` và `__` trước khi gửi khách. Không dùng regex. Câu đã
  sạch vẫn trả nguyên văn.
- `*` lẻ, tiêu đề `#`, gạch đầu dòng **không** bị code xoá; chỉ luật prompt chặn chúng.

### D — Định nghĩa lại "ok" trong prompt agent Đặt lịch
- `ok`: đã tạo đề xuất, hoặc thông tin khách hỏi (giờ trống, lịch của khách) có trong kết quả
  tool.
- Ngoại lệ, khớp với điều kiện của A: giờ khách hỏi bị kín **và không có đề xuất nào được
  tạo** thì không bao giờ là `ok`, mà là `need_info` với `question_field` = `time`.
- Dòng "`slot_unavailable` carries `nearest`: offer those" nói rõ là đưa các giờ đó qua
  `need_info`.

### E — Luật dữ kiện `taken` trong prompt Orchestrator
- Dữ kiện giờ có role `taken` là giờ đã có người: nói cho khách biết, không bao giờ mời hay
  nói giờ đó còn trống.
- Khi gợi ý giờ **khác**, chỉ dùng giờ role `free` hoặc các lựa chọn của `need_info`. Không
  cản việc nêu giờ của đề xuất mới.

### F — Phát hiện giờ kín thì phải báo khách
- **F1.** Câu `question_text` code viết cho A nói rõ phải báo khách trước:
  *"The time the customer asked for is taken. Tell the customer, then ask which of the offered
  times they want."* (biến thể không có giờ trống: *"... ask which other time they want."*).
- **F2.** Guard: mọi `TimeFact(role="taken")` của lượt thêm `spoken` vào `must_mention`. Câu
  trả lời thiếu giờ đó thì bị mã `missing_fact` (cơ chế sẵn có), viết lại rồi câu mẫu. Áp cho
  mọi trường hợp có giờ kín, kể cả khi lượt có đề xuất ("3 giờ đã có khách, em giữ chỗ 4 giờ,
  chị đồng ý không ạ?").
- **F3.** Câu mẫu dự phòng: lượt có giờ kín thì báo trước, ví dụ *"Dạ {at} đã có khách rồi ạ."*,
  rồi tới câu của nhánh đề xuất / R0 / phiên vừa mở / câu chung. Nhánh ghi thất bại và duyệt
  xong giữ nguyên.
- F2 kiểm câu có **nhắc** giờ kín. Nó không kiểm câu nói giờ đó kín hay trống; phần đó là E,
  và sau này là mức C.

## Ràng buộc (CONTEXT.md)
- Prompt viết tiếng Anh, không câu mẫu tiếng Việt (bẫy 17–19, `tests/salon/test_prompts.py`).
  Câu mẫu dự phòng là câu tiếng Việt cố định trong code, không phải prompt.
- Gói `salon/` không có regex (`tests/test_regex_budget.py`).
- Không chạy Azure khi chưa hỏi chủ dự án (quyết định 7). Đổi prompt thì phải đọc transcript
  chạy thật (bẫy 20).
- Commit message tiếng Anh. Không add `docs/superpowers/review.md`, `image.png`,
  `scripts/terminal_chat.py`.

## Ngoài phạm vi
- Mức C: tham chiếu dữ kiện trong câu trả lời. Spec riêng.
- Kiểm lộ prompt / lọt tiếng Anh; lưu câu bị guard loại vào `salon_turns`. Đề xuất gộp vào
  spec mức C.
- Luật guard `missing_option` (mục B, đã bỏ).

## Tiêu chí xong
- Test mới đỏ trước, xanh sau. Toàn bộ pytest và vitest xanh.
- Chạy thật lần 3 (sau khi chủ dự án đồng ý), đọc tay transcript:
  - lượt 2 báo 3 giờ đã có khách, hỏi khách chọn giờ trống, không mời 3 giờ;
  - lượt 2 mở phiên chờ (`time`);
  - không câu nào có `**`.
