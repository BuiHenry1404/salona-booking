# NOTE — bắt đầu từ đây

**Đọc file này trước** khi tiếp tục dự án. *Tại sao* nằm ở
[`CONTEXT.md`](CONTEXT.md), *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md), *cần gì
để lên prod* nằm ở [`PROD_CHECKLIST.md`](PROD_CHECKLIST.md).

Cập nhật: 2026-09-14 (nhánh `feat/natural-voice-guard`, Task 6)

## Đang ở đâu

**Nhánh `feat/natural-voice-guard`** (mọc từ `henry/develop`, chưa merge) đã
xong 6 task: node `guard` (3 phép kiểm tất định: `repeat`/`pronoun`/`clock`,
`address`+`register` gộp vào `pronoun`, `language` bị bỏ — bẫy #21), node
`rewrite` một lần, node `phrase` viết câu chốt lịch theo mạch với số liệu ép
bởi guard, neo giờ thiếu buổi/ngày trong `parse_time`, và `booking` 7 tool trả
lời câu kép shop+lịch. **721 test xanh** (`PYTHONPATH=. .venv/bin/python -m
pytest -q`, 14 deselected vì cần Azure thật). Chạy thật hai kịch bản
(`tu_nhien` mới + `dai`) qua Azure `gpt-5.4-mini`: cả 8 điểm kiểm của
`tu_nhien` đều đúng; log tầng gác gần như im lặng (1 `guard_violation` →
1 `guard_gave_up`, không log nào khác). Xem Checkpoint Task 6 cuối
`CONTEXT.md` và mục "Kiểm tầng gác" ở `RUNBOOK.md`.

## Việc tiếp theo (nhánh `feat/natural-voice-guard`)

Cả 6 task của spec `2026-09-14-natural-voice-guard-design.md` đã xong, đã đo
thật, đã ghi tài liệu (mục này). **Chưa merge vào `henry/develop`** — quyết
định merge thuộc chủ dự án, không tự merge. Còn để ngỏ (quan sát, không phải
lỗi cần sửa ngay):

- Một lượt trong 26 lượt đo được bị `guard_violation{repeat}` rồi
  `guard_gave_up` — mẫu quá nhỏ (1/26) để quyết có nên hạ `REPEAT_RATIO` hay
  không; cần thêm dữ liệu chạy thật trước khi đổi ngưỡng.
- Rubric `dai` nhiễu giữa các lần chấm cùng baseline (2.2 vs 2.4 đo được ở
  cùng ngày) — đừng dùng một lần chấm để kết luận cải thiện/suy giảm, xem bẫy
  #16 và #18.
- Probe supervisor 18 mẫu (bộ mới, thêm 4 câu kép shop+booking từ Task 5) nên
  chạy lại một lần nữa trước khi merge, để có mốc ứng với đúng bộ câu hiện tại
  (đợt đo Task 6 không chạy lại vì không đổi `SUPERVISOR_PROMPT`).

## Đang ở đâu (cũ)

**Cả 4 plan gốc đã xong** — backend, agent/memory/streaming, Telegram bot,
React frontend, đã merge vào `main`.

> ⚠️ **Từ đó tới nay có thêm ba đợt việc, đã merge vào `henry/develop`
> ngày 2026-09-14 nhưng `main` CHƯA nhận.** Trước khi làm bất cứ gì, đọc mục
> **Checkpoint 2026-09-14** ở cuối [`CONTEXT.md`](CONTEXT.md) — nó ghi đủ đã
> làm gì, còn dở gì, và ba bug tồn đọng kèm cách tái hiện.

- **630 test backend + 252 test frontend xanh**, frontend build sạch. (Tăng từ
  590 vì nhánh `feat/conversation-digest`, Task 1–6 — xem checkpoint cuối
  `CONTEXT.md`.)
- `scripts/live_e2e.py` — 31/31 pass trên server thật.
- `scripts/chat_scenarios.py` — 8 kịch bản hội thoại đúng.
- `scripts/llm_scenarios.py` — diễn lại kịch bản `LLM-xx`, dùng để bắt lỗi
  giọng điệu mà pytest không thấy.
- Chat chạy thật với Azure `gpt-5.4-mini`. Langfuse có trace và có chi phí.

**Quy ước nhánh:** mỗi việc một nhánh riêng → merge vào `henry/develop` →
`main` chỉ nhận bằng cách merge `henry/develop`. Không commit thẳng lên `main`.

## Việc tiếp theo — đã chốt

**Tầng digest xong** trên `feat/conversation-digest` (Task 1–6): `Digest`
model, `DigestService.maybe_compact`, `context_window`, chạy nền sau mỗi lượt,
đo thật kịch bản `dai` + rubric — xem checkpoint cuối `CONTEXT.md`. Nhánh này
**chưa merge vào `henry/develop`** — quyết định merge thuộc chủ dự án.

**Ba nhánh đã merge vào `henry/develop` và đã push** (2026-09-14). `main` chưa
nhận — merge lên `main` khi nào bạn thấy sẵn sàng.

**Ba bug nghiệp vụ** tìm được khi chạy hội thoại thật ngày 2026-09-13/14 (dời
lịch thành đặt thêm, hủy hụt vì mất id, viết số bằng chữ) **đã sửa cùng ngày**
trên `fix/reschedule-and-cancel`, rồi đợt hai `fix/cancel-confirm-and-prompts`
(hủy qua bước xác nhận, rule 4 hết bắn nhầm khi hết lịch, note chỉ là dịch vụ);
rồi `fix/rule4-english` (bỏ câu thoại sẵn cuối cùng trong prompt — rule 4 giờ
là mô tả tiếng Anh viết hoa); cả ba đã merge vào `henry/develop`. Chi tiết ở
Checkpoint trong `CONTEXT.md` (bẫy #19).

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
