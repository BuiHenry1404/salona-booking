# Bot Telegram cho chủ tiệm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Mỗi task là một file riêng; steps dùng checkbox (`- [ ]`).

**Goal:** Chủ tiệm nhận thông báo lịch mới trên điện thoại, tra lịch hôm nay/ngày mai, và bật tắt trạng thái bận/rảnh — tất cả bằng nút bấm trong Telegram, không phải gõ lệnh.

**Architecture:** Một chỗ tỏa tin duy nhất (`services/notifications.py`) đứng giữa nghiệp vụ và hai kênh Socket.IO + Telegram. Bot dùng long polling nên không cần domain public hay HTTPS. Không có AI trong nhánh này — bấm nút nào gọi service nấy, hoàn toàn tất định.

**Tech Stack:** httpx (gọi thẳng Telegram Bot API, không dùng framework), asyncio task trong lifespan của FastAPI.

Spec: [`06-telegram.md`](../../specs/2026-08-06-booking-nail-toc/06-telegram.md) · Lộ trình: [roadmap](../2026-08-06-booking-nail-toc-roadmap.md)

**Cần trước:** [Plan 1 — Nền tảng backend](../2026-08-06-backend-foundation/README.md). Không phụ thuộc Plan 2, chạy song song được.

## Ràng buộc toàn cục

Áp cho **mọi** task, kể cả khi file task không nhắc lại:

- **Đúng 1 worker.** Telegram chỉ chấp nhận một kết nối `getUpdates` cho mỗi bot token. Nhiều tiến trình sẽ đá nhau và nhận 409 Conflict không dứt.
- **Ủy quyền bằng `TELEGRAM_ADMIN_CHAT_IDS`.** Update từ chat_id khác bị **bỏ qua im lặng** — không trả lời gì, để người lạ dò ra bot cũng không biết nó làm gì. Bot đọc được tên và số điện thoại khách.
- **Không AI, không token.** Toàn bộ tất định. Test không cần LLM.
- **Nút bấm, không phải lệnh gõ tay.** Chủ tiệm lớn tuổi mà phải nhớ gõ `/homnay` là hỏng.
- **Fail-soft tuyệt đối.** Telegram lỗi, mạng chết hay token sai đều chỉ ghi log. Tuyệt đối không làm hỏng việc đặt lịch của khách.
- **Không thêm framework.** `python-telegram-bot` và `aiogram` đều thừa cho 4 nút và 2 loại thông báo — bàn phím lẫn nút inline chỉ là JSON.
- **Bot tắt được** bằng cách bỏ trống `TELEGRAM_BOT_TOKEN`. App vẫn chạy bình thường.

## Thứ tự task

| # | Task | Sản phẩm | Cần trước |
|---|---|---|---|
| 1 | [Chỗ tỏa tin](task-01-notifications.md) | `services/notifications.py` — Socket.IO + Telegram qua một cửa | — |
| 2 | [Client Telegram](task-02-telegram-client.md) | `sendMessage`, bàn phím, nút inline, `getUpdates` bằng httpx | — |
| 3 | [Xử lý nút bấm](task-03-handlers.md) | Ủy quyền, 4 nút chính, 4 nút chọn thời lượng | 2 |
| 4 | [Vòng lặp long polling](task-04-polling-loop.md) | Chạy trong lifespan, tắt được, chặn nhiều worker | 3 |
| 5 | [Nối vào nghiệp vụ](task-05-wire-services.md) | Đặt/hủy lịch báo Telegram, đổi bận/rảnh phát Socket.IO | 1, 3 |

Task 1 và 2 độc lập — chạy song song được.

## Hai chỗ dễ sai nhất

1. **Đổi bận/rảnh từ Telegram mà quên phát `shop_status_changed`.** Thẻ trạng thái trên máy khách và trang admin web sẽ hiển thị sai. Hai đường vào cùng một trạng thái thì phải cùng đi qua `ShopService` và cùng tỏa tin (test ở task 5).
2. **Gọi Telegram thẳng từ `AppointmentService`.** Làm vậy là bỏ qua chỗ tỏa tin, và Socket.IO với Telegram sẽ lệch nhau. Mọi thông báo đi qua `notifications.py`.

## Kiểm tra sau khi xong

```bash
pytest -v
```

Kiểm tay: tạo bot qua @BotFather, nhắn bot một câu rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates` để lấy chat_id, điền vào `.env`, khởi động app. Bot phải hiện bàn phím 4 nút. Bấm "Tôi đang bận" → chọn "30 phút" → gọi `GET /api/v1/shop/status` phải thấy `is_busy=true`. Đặt lịch qua API bằng tài khoản khác → Telegram phải nhận tin báo. Nhắn bot từ một tài khoản Telegram lạ → **không được** có phản hồi nào.
