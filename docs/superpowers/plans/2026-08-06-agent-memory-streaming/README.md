# Agent, memory và streaming — Implementation Plan

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Mỗi task là một file riêng; steps dùng checkbox (`- [ ]`).

**Goal:** Khách chat bằng tiếng Việt để hỏi chủ tiệm bận/rảnh và đặt lịch. Agent LangGraph gọi tool bọc quanh service của Plan 1, trace bằng Langfuse, và đẩy token cùng sự kiện tool về giao diện qua Socket.IO.

**Architecture:** `supervisor` phân loại ý định rồi định tuyến sang `StatusAgent`, `BookingAgent` hoặc `refuse`. Tool **không truy vấn DB**, chỉ bọc mỏng service. Hai tầng trí nhớ tách bạch: danh tính tất định từ JWT, lịch sử hội thoại từ Mongo. Không có tầng ngữ nghĩa — xem `CONTEXT.md` mục "Memory: vì sao chỉ hai tầng". Nhánh tắt `pending_confirmation` cho câu "ừ" đi thẳng vào thực thi, bỏ qua supervisor.

**Tech Stack:** LangGraph, langchain-openai (Azure), langfuse, python-socketio.

Spec: [`04-agent.md`](../../specs/2026-08-06-booking-nail-toc/04-agent.md) · Lộ trình: [roadmap](../2026-08-06-booking-nail-toc-roadmap.md)

**Cần trước:** [Plan 1 — Nền tảng backend](../2026-08-06-backend-foundation/README.md) đã xong. Plan này dùng `AppointmentService`, `ShopService`, `User`, `get_current_user`.

## Ràng buộc toàn cục

Áp cho **mọi** task, kể cả khi file task không nhắc lại:

- **AI chỉ để đặt lịch.** Thực thi bằng ba lớp: nhánh `refuse` tường minh trong đồ thị; mỗi subagent chỉ được cấp đúng bộ tool của nó; `user_id` bơm từ JWT chứ không đọc từ nội dung tin nhắn.
- **Tool không truy vấn DB.** Mọi tool gọi service của Plan 1. Logic chặn trùng giờ chỉ tồn tại một chỗ.
- **Không có tool nào ghi lịch.** Agent chỉ `propose_appointment` (giữ chỗ + đặt cờ `pending_confirmation`); lịch được tạo ở node `confirm` sau khi khách đồng ý. Quy tắc "xác nhận trước khi ghi" là ràng buộc cấu trúc, không phải lời dặn trong prompt.
- **Tên gọi khách không bao giờ đến từ memory ngữ nghĩa.** Danh tính đi thẳng từ JWT vào khối bối cảnh dựng bằng code. Prompt nói rõ: memory mâu thuẫn với khối bối cảnh thì tin khối bối cảnh.
- **Bố cục prompt xếp theo độ ổn định:** System (tĩnh, được cache) → Messages (lịch sử) → Message (khối bối cảnh + memory, đổi mỗi lượt) → Message (tin mới). Không nhét khối bối cảnh vào system prompt.
- **Chỉ stream token mang tag `respond`** — tag gắn cho model của hai subagent, nơi sinh câu trả lời cuối. Token của supervisor và của parser thời gian là JSON; lọt ra màn hình là rác.
- **Fail-soft cho memory và trace.** `recall` lỗi hoặc quá 2 giây thì trả rỗng. Langfuse lỗi thì nuốt. Không thứ nào được làm hỏng một lượt chat.
- **Mọi tích hợp ngoài tắt được bằng cách bỏ trống biến env.** Thiếu `LANGFUSE_*` thì không trace, app vẫn chạy.
- **Python 3.11+.**

## Thứ tự task

| # | Task | Sản phẩm | Cần trước |
|---|---|---|---|
| 1 | [Phụ thuộc và LLM client](task-01-llm-and-langfuse.md) | `AzureChatOpenAI` factory, `app/core/langfuse.py` tắt được | — |
| 3 | [Hội thoại và lịch sử](task-03-conversation.md) | Lưu tin nhắn, cắt lịch sử theo ngân sách token, `pending_confirmation` | — |
| 4 | [State và khối bối cảnh](task-04-state-and-context.md) | `GraphState`, `build_context_block()` tất định | 3 |
| 4b | [Parser thời gian tiếng Việt](task-04b-timeparse.md) | `parse_vi_time()` — regex đường tắt, LLM đỡ phần còn lại, một lớp chốt | 1 |
| 5 | [Tool](task-05-tools.md) | 6 tool bọc service, `user_id` đóng kín trong closure | 4b |
| 6 | [Supervisor và refuse](task-06-supervisor.md) | Định tuyến ý định, chặn câu ngoài chủ đề | 1, 4 |
| 7 | [Hai subagent](task-07-subagents.md) | `StatusAgent`, `BookingAgent` với vòng lặp tool | 5, 6 |
| 8 | [Nhánh xác nhận](task-08-confirm-and-save.md) | Nhánh tắt `pending_confirmation` | 3, 4 |
| 9 | [Ráp đồ thị và phát sự kiện](task-09-graph-and-events.md) | `astream_events` → 5 sự kiện Socket.IO, lọc theo tag | 6, 7, 8 |
| 10 | [Socket.IO chat handler](task-10-socketio-chat.md) | Endpoint chat thật, thay handler cũ của template | 9 |

Task 3 độc lập với task 1 — chạy song song được. Task 5 giờ cần task 4b.

Số 2 bỏ trống: task memory adapter đã xoá cùng quyết định bỏ Mem0. Giữ nguyên số của các task còn lại vì chúng được tham chiếu chéo ở nhiều chỗ.

Đánh số `4b` thay vì chèn `5` rồi dồn cả loạt: task 5–10 được tham chiếu chéo ở nhiều chỗ, đổi số là phải sửa hết và dễ sót.

## Ba chỗ dễ sai nhất

1. **Stream nhầm token của supervisor — hoặc của parser.** Gắn `tags=["respond"]` cho model của hai subagent và chỉ chuyển tiếp `on_chat_model_stream` mang tag đó. Model của parser thời gian phải mang `tags=["timeparse"]` và `streaming=False`, nếu không khách sẽ thấy `{"start_at": "2026-08-...` chạy ngang giữa cuộc trò chuyện. Test ở task 4b và task 9.
2. **Tự thêm lại tool ghi lịch.** Nếu thấy "agent không đặt được lịch trong một lượt" mà thêm `create_appointment` vào bộ tool thì hỏng cả hai lớp bảo vệ: khách mất bước xác nhận, và chuỗi ISO quay lại đi vòng qua model. Test `test_there_is_NO_tool_that_writes_an_appointment` ở task 5 canh chỗ này.
3. **Nới regex của parser thời gian.** Nó chỉ được trả lời khi câu khớp trọn vẹn; thiếu ngày hoặc thiếu giờ là nhường cho LLM. Thêm mẫu "thứ Năm" là mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — mà LLM không bao giờ được gọi để sửa. Lớp `TestRegexDefers` ở task 4b canh chỗ này.

## Kiểm tra sau khi xong

```bash
pytest -v
```

Và kiểm tay: mở `static/socketio_test.html` (hoặc client tạm), đăng nhập rồi làm đủ **hai lượt**:

1. Gõ `"mai 3h chiều làm tóc được không con"` — phải thấy lần lượt `turn_started` → `tool_started`/`tool_finished` cho `parse_time` rồi `propose_appointment` → nhiều `token` → `complete`. AI phải **hỏi lại xác nhận**, và `GET /api/v1/appointments/mine` lúc này vẫn **rỗng**.
2. Gõ `"ừ"` — lịch mới xuất hiện trong `GET /api/v1/appointments/mine`. Lượt này **không được** có sự kiện `tool_started` nào và phải trả lời gần như tức thì: nó đi nhánh `confirm`, không gọi LLM.

Gõ `"cháu bán bảo hiểm không"` phải bị `refuse` từ chối lịch sự.
