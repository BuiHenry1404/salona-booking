# NOTE — bắt đầu từ đây

**Đọc file này trước** khi tiếp tục dự án. *Tại sao* nằm ở
[`CONTEXT.md`](CONTEXT.md), *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md).

Cập nhật: 2026-08-19

## Đang ở đâu

- Plan 1 (backend) và Plan 2 (agent, memory, streaming): **xong**. 283 test xanh,
  10 test gọi Azure thật xanh, 31 case đầu-cuối trên server thật xanh
  (`scripts/live_e2e.py`), 8 kịch bản hội thoại chạy đúng (`scripts/chat_scenarios.py`).
- Nhánh `feat/agent-memory-streaming`, 25 commit, đã mở **PR #1** vào `main`
  (https://github.com/BuiHenry1404/salona-booking/pull/1) — chưa merge.
- Chat đặt lịch chạy thật với Azure `gpt-5.4-mini`. Langfuse tự dựng, có trace và
  có chi phí.

## Việc tiếp theo — đã chốt

Chủ tiệm ngồi máy tính nên bảng điều khiển web là kênh chính, Telegram chỉ là phụ.
Vì vậy **tạm bỏ qua bot Telegram**, nhưng vẫn phải lấy hai task realtime của Plan 3
(chúng không dính gì tới Telegram):

1. **Plan 3 task 1** — `plans/2026-08-06-telegram-bot/task-01-notifications.md`
   ("Chỗ tỏa tin duy nhất") và **task 5** — `task-05-wire-services.md`
   ("Nối thông báo vào nghiệp vụ"). Plan 2 mới dựng sẵn
   `SocketIOService.broadcast` và `emit_to_admins` mà **chưa ai gọi**; hai task này
   nối chúng vào `AppointmentService` / `ShopService`. Bỏ qua thì lịch mới không tự
   nhảy lên màn hình chủ tiệm, phải F5.
2. **Plan 4 — React**, `plans/2026-08-06-react-frontend/`, 9 task. Làm sau bước 1
   thì task 6 (bảng admin) cắm thẳng vào sự kiện realtime, không phải sửa lại.
3. Bot Telegram (Plan 3 task 2–4) khi nào cần.

## Để sau

- [ ] **mongo-express** — đang bật cùng `docker compose up`, đăng nhập `admin`/`admin`
      hardcode. Chọn một: thêm `profiles: ["tools"]` để mặc định không bật, hoặc xoá
      hẳn và soi DB bằng `docker compose exec mongo mongosh salon_booking`.
- [ ] **Merge PR #1** (`feat/agent-memory-streaming` → `main`).
- [ ] **Đặt lại mật khẩu admin** — cần bot Telegram tồn tại trước, xem
      `specs/2026-08-15-admin-password-reset-telegram-design.md`.
- [ ] **Plan 5 — vận hành**, chưa viết: nginx + HTTPS, `docker-compose.prod.yml`,
      sao lưu Mongo, CI, throttle toàn cục. Danh sách đầy đủ ở mục "Chưa có" trong
      CONTEXT.md.
- [ ] **Avatar chatbot** — chưa sinh; mockup đang trỏ tới `ai-avatar.jpg` không có
      trong git.

## Thiết kế Mongo — đã rà 2026-08-17, không phải sửa ngay

Bảy collection: `appointments`, `users`, `conversations`, `rate_limits`,
`refresh_tokens`, cộng `shop_hours` và `shop_status` (tạo lười lúc ghi đầu tiên,
không có trong `ensure_indexes`). Index đủ, không có truy vấn nào quét toàn bộ.
Hai điều ghi lại để khỏi giật mình khi gặp:

- **`conversations` phình vô hạn.** Mỗi khách một document, tin cũ không bị xoá, và
  `history()` nạp cả document mỗi lượt chat rồi mới lọc bằng Python. Tính ra ~20KB
  sau một năm cho một khách bình thường nên chưa đáng làm gì. Nếu có khách chat
  hằng ngày thì vá bằng `{"messages": {"$slice": -50}}` trong `find_one`, không
  phải tách bảng.
- **`user_id` lưu dạng chuỗi, còn `users._id` là ObjectId.** Nhất quán trong app nên
  hiện không sai ở đâu, nhưng `$lookup` sang `users` sẽ không khớp vì lệch kiểu —
  sẽ vấp lúc viết báo cáo cho chủ tiệm. Đổi thì phải migrate bốn collection, chỉ
  làm khi có nhu cầu thật.

## Dễ quên

- **Fail-soft che lỗi.** Hai lỗi nặng nhất tới giờ đều không làm test nào đỏ:
  Socket.IO mount sai đường dẫn (test gọi thẳng `handle_message`), và timeout
  parser 2 giây khiến mọi lượt LLM hết giờ (fail-soft trả "thiếu giờ cụ thể" y
  như khi câu thật sự thiếu). Chỗ nào nuốt lỗi thì phải có kịch bản chạy thật
  soi vào — `scripts/chat_scenarios.py` sinh ra vì lý do đó.
- Dựng lại stack Langfuse từ đầu là **mất đơn giá model** → mọi trace hiện 0đ.
  Lệnh seed lại nằm ở đầu `docker-compose.langfuse.yml`.
- `docker compose down -v` xoá luôn volume: mất lịch của khách và toàn bộ trace.
  Muốn tắt thì `down` không có `-v`.
- Cấu hình **chỉ đọc từ `.env`**, không đọc biến môi trường của shell.
