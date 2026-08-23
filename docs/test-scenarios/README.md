# TỔNG HỢP TEST SCENARIOS — DỰ ÁN SALONA BOOKING

Thư mục này chứa toàn bộ các kịch bản kiểm thử (Test Scenarios), ca kiểm thử chi tiết (Test Cases), kịch bản đối thoại với AI (LLM Chat Scenarios), và ma trận trường hợp biên (Edge Cases) cho hệ thống đặt lịch Salona Booking.

---

## CẤU TRÚC TÀI LIỆU

| Tài liệu | Mô tả | Nội dung chính |
|---|---|---|
| [**`01-system-test-scenarios.md`**](01-system-test-scenarios.md) | **Kiểm thử Toàn hệ thống (9 Tầng)** | Core utilities (phone, clock, slots, security), MongoDB repositories, Partial index, Business services, LangGraph Graph, Socket.IO streaming, Telegram bot, REST API, Frontend React tokens, Ma trận 14 Failure modes. |
| [**`02-llm-chat-scenarios.md`**](02-llm-chat-scenarios.md) | **Kịch bản Đối thoại Chi tiết với AI (LLM Chat Scenarios)** | Toàn bộ kịch bản hội thoại đa lượt (Multi-turn dialogues) giữa Khách lớn tuổi và AI Lễ tân: Đặt lịch, Tra cứu thời gian mơ hồ, Hỏi bận/rảnh, Xung đột slot & gợi ý, Xem/Huỷ lịch, Đổi ý giữa chừng, Chặn câu hỏi ngoài luồng & Jailbreak, Tiếng lóng/Tiếng Việt không dấu, Quá hạn cờ pending, và Phục hồi sự cố (Fail-soft). |

---

## NGUYÊN TẮC CỐT LÕI KHI KIỂM THỬ HỘI THOẠI AI
1. **Lễ tân luôn xưng "con", gọi khách theo danh xưng trong bối cảnh** (ví dụ "cô Lan", "chú Hùng", "bác Ba").
2. **AI không có tool ghi lịch trực tiếp** — Lượt 1 kiểm tra & giữ chỗ (`propose_appointment`), nhắc lại mốc giờ rõ ràng; Lượt 2 khách "ừ" thì node `confirm` thực thi (0 lượt LLM, đọc dữ liệu tất định từ MongoDB).
3. **Mốc thời gian luôn diễn đạt bằng GIỜ TUYỆT ĐỐI** ("xong lúc 3:30 chiều"), không bao giờ dùng khoảng đếm ngược ("còn 30 phút").
4. **Không đoán thời gian thiếu** — Thiếu ngày hỏi ngày, thiếu giờ hỏi giờ, thiếu buổi (sáng/chiều) hỏi buổi.
5. **Chỉ stream token mang tag `respond`** — Token JSON của supervisor và parser thời gian bị chặn hoàn toàn.
