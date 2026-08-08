# Frontend React — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Mỗi task là một file riêng; steps dùng checkbox (`- [ ]`).

**Goal:** Năm màn hình mobile-first cho người lớn tuổi — đăng nhập, chat có hiển thị streaming và trạng thái gọi tool, lịch của tôi, bảng điều khiển chủ tiệm, danh sách khách.

**Architecture:** Vite + React + TypeScript, app riêng gọi API qua CORS. Một `useAgentStream` hook gom toàn bộ sự kiện Socket.IO thành một máy trạng thái duy nhất, nên màn hình chat chỉ việc vẽ. Design token dựng từ mockup đã duyệt.

**Tech Stack:** Vite, React 18, TypeScript, socket.io-client, Vitest + React Testing Library.

Spec: [`05-frontend.md`](../../specs/2026-08-06-booking-nail-toc/05-frontend.md) · Mockup: [`ui-mockup.html`](../../specs/2026-08-06-booking-nail-toc/ui-mockup.html), [`ui-mockup-streaming.html`](../../specs/2026-08-06-booking-nail-toc/ui-mockup-streaming.html)

**Cần trước:** [Plan 1](../2026-08-06-backend-foundation/README.md) và [Plan 2](../2026-08-06-agent-memory-streaming/README.md).

**Về realtime của màn hình chủ tiệm.** Plan 2 dựng `broadcast`/`emit_to_admins`, nhưng
chỗ *gọi* chúng nằm ở [Plan 3 task 5](../2026-08-06-telegram-bot/task-05-wire-services.md).
Chưa làm Plan 3 thì mọi màn hình vẫn chạy — nút bận/rảnh áp thẳng kết quả HTTP trả về —
chỉ là lịch mới không tự nhảy lên, chủ tiệm phải tải lại trang. Đừng đi lần mò trong
frontend khi gặp triệu chứng này.

## Ràng buộc toàn cục

Áp cho **mọi** task, kể cả khi file task không nhắc lại:

- **Cỡ chữ nền 19px.** Nút cao tối thiểu 56px và rộng hết chiều ngang.
- **Mỗi màn hình một hành động chính.** Không menu ẩn, không hamburger, không modal chồng nhau.
- **Mọi trạng thái diễn đạt bằng chữ**, không chỉ bằng màu. Người lớn tuổi hay bị lóa và mù màu nhẹ.
- **Tương phản tối thiểu 4.5:1.** Bảng màu đã kiểm: `--primary #0369A1` (5.93:1), `--accent #047857` (5.48:1), `--danger #B91C1C` (5.91:1). **Không** dùng `#0284C7` (4.10:1) hay `#059669` (3.77:1) cho chữ.
- **Font Be Vietnam Pro.** Atkinson Hyperlegible dễ đọc hơn nhưng **không có bộ ký tự tiếng Việt** — mọi chữ có dấu sẽ rơi sang font dự phòng.
- **Không hiện tên tool kỹ thuật cho khách.** `find_free_slots` phải thành "Đang xem lịch trống…".
- **Tôn trọng `prefers-reduced-motion: reduce`** — tắt mọi hiệu ứng nhấp nháy và xoay.
- **Icon là SVG inline**, không dùng emoji làm icon.
- **Route admin chặn ở backend**, frontend chỉ ẩn cho gọn — không coi việc ẩn là bảo mật.

## Thứ tự task

| # | Task | Sản phẩm | Cần trước |
|---|---|---|---|
| 1 | [Khởi tạo và design token](task-01-scaffold.md) | Vite + TS + `tokens.css` + Vitest chạy được | — |
| 2 | [API client và đăng nhập](task-02-auth.md) | `api.ts`, lưu token, route bảo vệ, màn hình đăng nhập | 1 |
| 3 | [Hook streaming](task-03-agent-stream.md) | `useAgentStream` — gom 6 sự kiện thành một máy trạng thái | 1 |
| 4 | [Màn hình chat](task-04-chat-screen.md) | Thẻ trạng thái tiệm, bong bóng chat, tiến trình gọi tool | 2, 3 |
| 5 | [Lịch của tôi](task-05-my-appointments.md) | Danh sách thẻ lớn, hủy có xác nhận tại chỗ | 2 |
| 6 | [Bảng điều khiển chủ tiệm](task-06-admin-dashboard.md) | Nút bận/rảnh khổng lồ, lịch hôm nay realtime | 2, 3 |
| 7 | [Khách hàng và giờ mở cửa](task-07-admin-customers.md) | Tạo tài khoản, tìm theo SĐT, sửa giờ mở cửa | 2 |
| 8 | [Kiểm tra và đóng gói](task-08-build-and-verify.md) | Build production, rà accessibility, hướng dẫn deploy | 1–7 |

## Năm chỗ dễ sai nhất

1. **Hiện tên tool thô cho khách.** Bảng ánh xạ nằm ở frontend (task 3) để đổi câu chữ không phải deploy lại API. Tool lạ không có trong bảng thì hiện "Đang xử lý…", không bao giờ hiện tên thô.
2. **Xóa chữ đã hiện khi mất mạng.** Giữ nguyên phần đã stream và thêm dòng "Mất mạng, đang thử lại…". Xóa đi thì người lớn tuổi tưởng mình làm hỏng.
3. **Dùng màu để phân biệt bận/rảnh mà không có chữ.** Luôn kèm chữ "Đang bận" / "Đang rảnh" cỡ lớn.
4. **Bỏ mất câu khách gõ lúc mất mạng.** `send` phải xếp câu vào hàng đợi và tự gửi lại khi nối lại (task 3), bong bóng hiện ngay kèm "Đang gửi lại…". Bắt một cụ 70 tuổi gõ lại câu vừa gõ là mất luôn khách.
5. **Mỗi hook tự mở một socket.** socket.io-client gộp theo URL nên hai lần `io(BASE)` trả về **cùng** một socket; hook nào unmount trước sẽ ngắt kết nối của hook kia. Dùng `acquireSocket`/`releaseSocket` (task 3) và mỗi hook tự `off` handler của mình.

## Kiểm tra sau khi xong

```bash
cd frontend && npm test && npm run build
```

Và kiểm tay trên điện thoại thật: đăng nhập bằng SĐT, gõ "mai 3h chiều làm tóc được không con" — phải thấy ba chấm, rồi dòng "Đang xem lịch trống…", rồi chữ hiện dần. Bấm bận 30 phút ở máy admin, thẻ trạng thái trên máy khách phải đổi ngay không cần tải lại.
