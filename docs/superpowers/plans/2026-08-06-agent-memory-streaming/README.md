# Agent, memory và streaming — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Mỗi task là một file riêng; steps dùng checkbox (`- [ ]`).

**Goal:** Khách chat bằng tiếng Việt để hỏi chủ tiệm bận/rảnh và đặt lịch. Agent LangGraph gọi tool bọc quanh service của Plan 1, có memory ngữ nghĩa qua Mem0/pgvector, trace bằng Langfuse, và đẩy token cùng sự kiện tool về giao diện qua Socket.IO.

**Architecture:** `supervisor` phân loại ý định rồi định tuyến sang `StatusAgent`, `BookingAgent` hoặc `refuse`. Tool **không truy vấn DB**, chỉ bọc mỏng service. Ba tầng trí nhớ tách bạch: danh tính tất định từ JWT, lịch sử hội thoại từ Mongo, ngữ nghĩa từ Mem0. Nhánh tắt `pending_confirmation` cho câu "ừ" đi thẳng vào thực thi, bỏ qua supervisor.

**Tech Stack:** LangGraph, langchain-openai (Azure), mem0ai, psycopg, langfuse, python-socketio.

Spec: [`04-agent.md`](../../specs/2026-08-06-booking-nail-toc/04-agent.md) · Lộ trình: [roadmap](../2026-08-06-booking-nail-toc-roadmap.md)

**Cần trước:** [Plan 1 — Nền tảng backend](../2026-08-06-backend-foundation/README.md) đã xong. Plan này dùng `AppointmentService`, `ShopService`, `User`, `get_current_user`.

## Ràng buộc toàn cục

Áp cho **mọi** task, kể cả khi file task không nhắc lại:

- **AI chỉ để đặt lịch.** Thực thi bằng ba lớp: nhánh `refuse` tường minh trong đồ thị; mỗi subagent chỉ được cấp đúng bộ tool của nó; `user_id` bơm từ JWT chứ không đọc từ nội dung tin nhắn.
- **Tool không truy vấn DB.** Mọi tool gọi service của Plan 1. Logic chặn trùng giờ chỉ tồn tại một chỗ.
- **Tên gọi khách không bao giờ đến từ memory ngữ nghĩa.** Danh tính đi thẳng từ JWT vào khối bối cảnh dựng bằng code. Prompt nói rõ: memory mâu thuẫn với khối bối cảnh thì tin khối bối cảnh.
- **Bố cục prompt xếp theo độ ổn định:** System (tĩnh, được cache) → Messages (lịch sử) → Message (khối bối cảnh + memory, đổi mỗi lượt) → Message (tin mới). Không nhét khối bối cảnh vào system prompt.
- **Chỉ stream token của node `respond`.** Token của supervisor là JSON định tuyến; lọt ra màn hình là rác.
- **Fail-soft cho memory và trace.** `recall` lỗi hoặc quá 2 giây thì trả rỗng. Langfuse lỗi thì nuốt. Không thứ nào được làm hỏng một lượt chat.
- **Mọi tích hợp ngoài tắt được bằng cách bỏ trống biến env.** Thiếu `POSTGRES_URI` thì memory tắt, thiếu `LANGFUSE_*` thì không trace, app vẫn chạy.
- **Python 3.11+.**

## Thứ tự task

| # | Task | Sản phẩm | Cần trước |
|---|---|---|---|
| 1 | [Phụ thuộc và LLM client](task-01-llm-and-langfuse.md) | `AzureChatOpenAI` factory, `app/core/langfuse.py` tắt được | — |
| 2 | [Memory adapter](task-02-memory-adapter.md) | `recall()` / `remember()` bọc Mem0, fail-soft, kiểm số chiều lúc khởi động | 1 |
| 3 | [Hội thoại và lịch sử](task-03-conversation.md) | Lưu tin nhắn, cắt lịch sử theo ngân sách token, `pending_confirmation` | — |
| 4 | [State và khối bối cảnh](task-04-state-and-context.md) | `GraphState`, `build_context_block()` tất định | 2, 3 |
| 4b | [Parser thời gian tiếng Việt](task-04b-timeparse.md) | `parse_vi_time()` — regex đường tắt, LLM đỡ phần còn lại, một lớp chốt | 1 |
| 5 | [Tool](task-05-tools.md) | 6 tool bọc service, `user_id` đóng kín trong closure | 4b |
| 6 | [Supervisor và refuse](task-06-supervisor.md) | Định tuyến ý định, chặn câu ngoài chủ đề | 1, 4 |
| 7 | [Hai subagent](task-07-subagents.md) | `StatusAgent`, `BookingAgent` với vòng lặp tool | 5, 6 |
| 8 | [Xác nhận và ghi memory](task-08-confirm-and-save.md) | Nhánh tắt `pending_confirmation`, `save_memory` chạy nền | 3, 4 |
| 9 | [Ráp đồ thị và phát sự kiện](task-09-graph-and-events.md) | `astream_events` → 5 sự kiện Socket.IO, lọc theo tag | 6, 7, 8 |
| 10 | [Socket.IO chat handler](task-10-socketio-chat.md) | Endpoint chat thật, thay handler cũ của template | 9 |

Task 3 độc lập với task 1–2 — chạy song song được. Task 5 giờ cần task 4b.

Đánh số `4b` thay vì chèn `5` rồi dồn cả loạt: task 5–10 được tham chiếu chéo ở nhiều chỗ, đổi số là phải sửa hết và dễ sót.

## Năm chỗ dễ sai nhất

1. **Stream nhầm token của supervisor — hoặc của parser.** Gắn `tags=["respond"]` cho model của node `respond` và chỉ chuyển tiếp `on_chat_model_stream` mang tag đó. Model của parser thời gian phải mang `tags=["timeparse"]` và `streaming=False`, nếu không khách sẽ thấy `{"start_at": "2026-08-...` chạy ngang giữa cuộc trò chuyện. Test ở task 4b và task 9.
2. **Số chiều embedding lệch.** pgvector **im lặng** nuốt lỗi ghi — API trả về thành công kèm memory ID nhưng không lưu gì. Kiểm tra lúc khởi động (task 2).
3. **Memory rò giữa các user.** Mọi lời gọi Mem0 phải truyền `user_id`. Test cô lập A/B là bắt buộc (task 2).
4. **Mem0 có thể chỉ có API đồng bộ.** Adapter chạy nó trong threadpool; đừng gọi thẳng trong event loop (task 2).
5. **Nới regex của parser thời gian.** Nó chỉ được trả lời khi câu khớp trọn vẹn; thiếu ngày hoặc thiếu giờ là nhường cho LLM. Thêm mẫu "thứ Năm" là mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — mà LLM không bao giờ được gọi để sửa. Lớp `TestRegexDefers` ở task 4b canh chỗ này.

## Kiểm tra sau khi xong

```bash
pytest -v
```

Và kiểm tay: mở `static/socketio_test.html` (hoặc client tạm), đăng nhập, gõ "mai 3h chiều làm tóc được không con" — phải thấy lần lượt `turn_started` → `tool_started` → `tool_finished` → nhiều `token` → `complete`, và lịch xuất hiện trong `GET /api/v1/appointments/mine`. Gõ "cháu bán bảo hiểm không" phải bị `refuse` từ chối lịch sự.
