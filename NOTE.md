# NOTE — bắt đầu từ đây

**Đọc file này trước** khi tiếp tục dự án. *Tại sao* nằm ở
[`CONTEXT.md`](CONTEXT.md), *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md), *cần gì
để lên prod* nằm ở [`PROD_CHECKLIST.md`](PROD_CHECKLIST.md).

Cập nhật: 2026-09-19

## Đang ở đâu

**Tầng Conversation State đã xong** (nhánh `feat/conversation-state`, chưa
merge): `Digest` đổi tên thành `ConversationState` (`summary` + `slots`).
Slots là dữ kiện dạng trường (ý định, dịch vụ, ngày/giờ đang nhắm, lịch bị từ
chối) do LLM sinh ở lượt nén nền, lọc lại bằng code (`sanitize_slots`) rồi
dựng thành chữ (`render_slots`) và nhồi chung một message với `summary`, ngay
sau system prompt. Không có đường code nào đặt/dời/hủy lịch dựa trên slots —
`test_no_code_path_books_from_slots` canh (xem `CONTEXT.md` bẫy #22).

Trước đó, toàn bộ đợt việc 12–14/9 đã merge vào `henry/develop` và đã push:
định tuyến lại agent, hội thoại tự nhiên, ba bug nghiệp vụ (dời/hủy/viết số),
bỏ câu thoại sẵn khỏi prompt, tầng digest trong phiên, ba lỗi từ chat thật, và
tầng gác `guard`/`rewrite`/`phrase` + neo thời gian + `booking` 7 tool.

- **764 test backend xanh, 1 skip** (tự skip sau 3 giờ chiều giờ VN, bình
  thường) **+ 252 test frontend xanh**, frontend build sạch.
  (`PYTHONPATH=. .venv/bin/python -m pytest -q`, 14 deselected vì cần Azure thật.)
- **PR #6 đang mở**: `henry/develop` → `main`, 85 commit / 13 merge.
  `main` chỉ nhận qua PR này. `feat/conversation-state` chưa merge vào
  `henry/develop` — việc merge để người quyết, không tự động ở đây.
- Nhật ký từng đợt, kết quả đo và các quan sát còn để ngỏ: nửa sau
  [`CONTEXT.md`](CONTEXT.md) ("Nhật ký các đợt việc").

**Quy ước nhánh:** mỗi việc một nhánh riêng → merge vào `henry/develop` →
`main` chỉ nhận bằng cách merge `henry/develop`. Không commit thẳng lên `main`.

## Việc tiếp theo

1. **Merge PR #6** khi thấy sẵn sàng.
2. Hai mục **P1** trong `REPO_AUDIT.md` (bên dưới) — cần xong trước khi mở cho
   khách thật.
3. Bốn quan sát đã đo nhưng **chưa quyết** (chi tiết ở mục "Việc còn dở" của
   `CONTEXT.md`): đo token digest khi Langfuse sống; hai lần từ chối liên tiếp
   ra câu gần y nhau; câu hỏi xác nhận bị tính là lặp; neo có thể ra giờ đã qua.

Còn **2 mục P1** trong [`REPO_AUDIT.md`](REPO_AUDIT.md), cần xong trước khi mở
cho khách thật:

1. **SEC-01 — Socket.IO không kiểm `token_version`.** Đổi mật khẩu thu hồi token
   trên HTTP, nhưng socket đang mở vẫn sống tới khi JWT hết hạn. Sửa ở
   `_authenticate` của `SocketIOService`, kèm test hồi quy.
2. **SEC-03 — tách `docker-compose.prod.yml`.** Bỏ `mongo-express`, bỏ cổng
   `27017:27017`, bật auth Mongo, bỏ mount source và `--reload`. Chi tiết từng
   bước ở `PROD_CHECKLIST.md` mục 1.1.

Sau đó là vận hành: HTTPS, sao lưu Mongo, CI — `PROD_CHECKLIST.md` bước 1–2.

## Để sau

- [ ] **DEP-01** — `python-jose` đã bỏ hoang và có CVE, thay bằng `PyJWT`.
- [ ] **REL-01** — hai lượt chat song song của cùng một khách giẫm nhau.
- [ ] **REL-05** — `conversations` phình vô hạn (xem mục Mongo bên dưới).
- [ ] **Đặt lại mật khẩu admin qua Telegram** — spec ở
      `docs/superpowers/specs/2026-08-15-admin-password-reset-telegram-design.md`.
- [ ] **Avatar chatbot** — chưa sinh; mockup trỏ `ai-avatar.jpg` không có trong git.
- [ ] **Dọn 3 bản trùng** của tài liệu test: `TEST_SCENARIOS.md` ở gốc repo trùng
      byte-for-byte với `docs/test-scenarios/TEST_SCENARIOS.md`, và
      `01-system-test-scenarios.md` chỉ khác đúng 1 dòng tiêu đề. `README.md` của
      thư mục đó chỉ trỏ tới `01-` và `02-`, nên hai file kia là dư.

## Thiết kế Mongo — đã rà 2026-08-17, không phải sửa ngay

Bảy collection: `appointments`, `users`, `conversations`, `rate_limits`,
`refresh_tokens`, cộng `shop_hours` và `shop_status` (tạo lười lúc ghi đầu tiên,
không có trong `ensure_indexes`). Index đủ, không có truy vấn nào quét toàn bộ.

- **`conversations` phình vô hạn.** Mỗi khách một document, tin cũ không bị xoá, và
  `history()` nạp cả document mỗi lượt chat rồi mới lọc bằng Python. ~20KB sau một
  năm cho một khách bình thường nên chưa đáng làm gì. Khách chat hằng ngày thì vá
  bằng `{"messages": {"$slice": -50}}` trong `find_one`, không phải tách bảng.
- **`user_id` lưu dạng chuỗi, `users._id` là ObjectId.** Nhất quán trong app nên
  hiện không sai ở đâu, nhưng `$lookup` sang `users` sẽ không khớp vì lệch kiểu —
  sẽ vấp lúc viết báo cáo cho chủ tiệm. Đổi thì phải migrate bốn collection.

## Dễ quên

- **Fail-soft che lỗi.** Ba lỗi nặng nhất tới giờ đều không làm test nào đỏ:
  Socket.IO mount sai đường dẫn, timeout parser 2 giây, và AI lặp nguyên văn câu
  trả lời với khách. Chỗ nào nuốt lỗi thì phải có kịch bản chạy thật soi vào —
  `scripts/chat_scenarios.py` và `scripts/llm_scenarios.py` sinh ra vì lý do đó.
- **Đổi prompt thì phải chạy `scripts/llm_scenarios.py`**, đừng tin pytest. 413
  test xanh suốt trong khi máy đang lặp câu với khách, và một lần siết prompt đã
  tự tạo lỗ hổng quyền riêng tư mà không test nào đỏ.
- **Container `app` trong compose có thể giữ cổng 8000 với code cũ.** Uvicorn
  local sẽ báo `address already in use` rồi tự chết, nhưng health vẫn trả 200 vì
  container cũ trả lời — rất dễ tưởng đang test code mới. Gặp thì
  `docker compose stop app`.
- **Trần chat 30 tin/giờ mỗi khách.** Test tay nhiều là chạm. Gỡ bằng
  `docker compose exec -T mongo mongosh salon_booking --quiet --eval 'db.rate_limits.deleteMany({})'`
  — lệnh này cũng gỡ khoá "sai mật khẩu 10 lần".
- Dựng lại stack Langfuse từ đầu là **mất đơn giá model** → mọi trace hiện 0đ.
  Lệnh seed lại nằm ở đầu `docker-compose.langfuse.yml`.
- `docker compose down -v` xoá luôn volume: mất lịch của khách và toàn bộ trace.
  Muốn tắt thì `down` không có `-v`.
- Cấu hình **chỉ đọc từ `.env`**, không đọc biến môi trường của shell.
- Chạy script trong `scripts/` phải có `PYTHONPATH=.`, không thì
  `ModuleNotFoundError: app`.
