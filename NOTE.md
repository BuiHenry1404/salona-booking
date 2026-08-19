# NOTE — việc đang treo

Sổ tay ngắn, cập nhật khi có gì đó bị hoãn. *Tại sao* nằm ở
[`CONTEXT.md`](CONTEXT.md), *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md).

Cập nhật: 2026-08-17

## Đang ở đâu

- Plan 1 (backend) và Plan 2 (agent, memory, streaming): **xong**. 283 test xanh,
  10 test gọi Azure thật xanh, 31 case đầu-cuối trên server thật xanh
  (`scripts/live_e2e.py`), 8 kịch bản hội thoại chạy đúng (`scripts/chat_scenarios.py`).
- Nhánh `feat/agent-memory-streaming` — 20 commit, đã push, **chưa merge vào `main`**.

## Để sau

- [ ] **mongo-express** — đang bật cùng `docker compose up`, đăng nhập `admin`/`admin`
      hardcode. Chọn một: thêm `profiles: ["tools"]` để mặc định không bật, hoặc xoá
      hẳn và soi DB bằng `docker compose exec mongo mongosh salon_booking`.
- [ ] **Merge `feat/agent-memory-streaming` vào `main`.**
- [ ] **Plan 3 — Telegram** (`plans/2026-08-06-telegram-bot/`). Sau đó mới làm được
      phần đặt lại mật khẩu admin.
- [ ] **Plan 4 — React** (`plans/2026-08-06-react-frontend/`). Song song Plan 3 được.
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
