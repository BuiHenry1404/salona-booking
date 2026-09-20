# NOTE — bắt đầu từ đây

**Đọc file này trước** khi tiếp tục dự án. *Tại sao* nằm ở
[`CONTEXT.md`](CONTEXT.md), *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md), *cần gì
để lên prod* nằm ở [`PROD_CHECKLIST.md`](PROD_CHECKLIST.md).

Cập nhật: 2026-09-20

## Đang ở đâu

**Đợt gần nhất — `fix/clock-and-dead-data` (chưa merge):** 12 giờ giờ đọc là
"12 giờ **trưa**" chứ không phải "12 giờ chiều" (tiệm mở 08:00–19:00 không nghỉ
trưa nên ô 12:00–12:45 đặt được hằng ngày — sai này chạm khách thường xuyên);
0 giờ đọc là "12 giờ đêm". Bỏ slot `target_appointment_id` — dữ liệu chết tốn
một truy vấn Mongo mỗi lượt nén mà không ai đọc. Tách mã `already_yours` khỏi
`slot_unavailable`: khách đã có đúng lịch ở giờ đó là ca duy nhất mà câu đáp
đúng **ngược hẳn** với "chọn giờ khác đi", và trước đó AI nói "em chưa giữ
được 4 giờ chiều" trong khi lịch ấy đang tồn tại.

**Đợt "tool trả JSON, không trả câu" đã xong** (nhánh
`refactor/tools-return-data`, chưa merge): cả 6 tool đặt lịch/xem lịch giờ
chỉ cấp dữ kiện dạng JSON (mốc thời gian là `{"iso","spoken"}`), không còn
câu tiếng Việt viết sẵn hay mệnh lệnh nhét trong kết quả trả về — mọi luật đó
đã dồn về `BOOKING_PROMPT`/`SHOP_PROMPT`. Câu lỗi cũ thành mã lỗi
(`slot_unavailable`, `bad_time_format`, `appointment_not_found`,
`not_your_appointment`, `bad_day_format`, `unavailable`); `parse_time` giữ
nguyên hợp đồng cũ. Xem `CONTEXT.md` bẫy #23.

**Đã chạy thật với Azure để nghiệm thu** (kịch bản LLM-32, hội thoại dài kèm
nén): soi tay transcript `hoi-thoai-dai-sau-refactor-tool.txt` (file cục bộ,
untracked — không có trong git, xem "Dễ quên" bên dưới), **không** thấy khoá
JSON hay chuỗi ISO nào rò ra câu nói với khách (`iso`, `held`, `slots`,
`error`, `spoken`, `+07:00`, `HH:MM`...), giờ vẫn đọc đúng giọng Việt ("4 giờ
chiều"), xưng hô đúng "em"/"anh chị", và `conversations.state.slots` trong
Mongo vẫn được LLM sinh và lưu đúng sau lượt nén.

**Sửa lại kết luận trước đó (đã sai) về so sánh với bản trước đợt này.**
Bản ghi cũ ở đây từng nói bản cũ (`hoi-thoai-dai-sau-khi-fix.txt`) "hallucinate"
tự nhận đã chốt một giờ chưa giữ được, còn bản mới gọi lại
`propose_appointment` và báo đúng `slot_unavailable` — coi đó là bằng chứng
lợi ích của đợt refactor này. **Kết luận đó sai**, lý do:

- Hai lần chạy rơi vào **hai trạng thái DB khác nhau**. Nguyên nhân gốc đã
  tái hiện: trong lượt "Mà làm móng bột như vậy mất khoảng bao lâu... việc gì
  cũng **phải** tính trước cho chắc", hàm `is_affirmative()`
  (`app/agents/booking_graph/confirm.py:22-26`) khớp chữ "phải" trong `_YES`
  và coi câu tán gẫu đó là ĐỒNG Ý, nên node `confirm` ghi lịch thật 16:00
  ngay giữa cuộc — ở CẢ HAI lần chạy, nhưng một lần 16:00 khi đó còn trống
  (lịch được tạo thật) và một lần 16:00 đã là lịch của chính khách (lịch cũ
  còn đó). Hai transcript vì vậy **không so sánh được** — chênh lệch giữa
  chúng không quy được cho tool trả JSON hay bất cứ gì ở đợt này.
- Vì lý do trên: bản cũ nói "đã chốt 4 giờ chiều" là **đúng sự thật** ở lần
  chạy đó (lịch thật tồn tại) — không phải bịa. Bản mới nói "em chưa giữ
  được 4 giờ chiều" trong khi lịch của khách **đang tồn tại** mới là câu
  **sai sự thật** — và nó từng bị ghi vào đây như một thắng lợi.
- Lợi ích **thật**, quan sát được, của đợt "tool trả JSON" là **giọng tự
  nhiên hơn**: bản cũ lặp khuôn "Chủ tiệm sẽ trả lời về..." bốn lần, bản mới
  không còn lặp câu đó.

Xem bug `is_affirmative` ở mục "Để sau" bên dưới — **chưa sửa** trong đợt
final-fix (2026-09-20), ngoài phạm vi và cần thiết kế riêng.

Trước đó, tầng Conversation State (`Digest` đổi tên thành `ConversationState`,
`summary` + `slots`) đã xong ở nhánh `feat/conversation-state`, chưa merge.
Slots là dữ kiện dạng trường do LLM sinh ở lượt nén nền, lọc lại bằng code
(`sanitize_slots`) rồi dựng thành chữ (`render_slots`). Không có đường code
nào đặt/dời/hủy lịch dựa trên slots — `test_no_code_path_books_from_slots`
canh (bẫy #22).

Trước nữa, toàn bộ đợt việc 12–14/9 đã merge vào `henry/develop` và đã push:
định tuyến lại agent, hội thoại tự nhiên, ba bug nghiệp vụ (dời/hủy/viết số),
bỏ câu thoại sẵn khỏi prompt, tầng digest trong phiên, ba lỗi từ chat thật, và
tầng gác `guard`/`rewrite`/`phrase` + neo thời gian + `booking` 7 tool.

- **805 test backend xanh, 0 fail** (`PYTHONPATH=. .venv/bin/python -m
  pytest -q`, 14 deselected vì cần Azure thật). **Chỉ chạy MỘT tiến trình
  pytest một lúc** — hai tiến trình tranh chấp cùng Mongo test DB và ra hàng
  chục lỗi pymongo giả.
- **Bốn nhánh đang xếp chồng, CHƯA nhánh nào merge**: `feat/conversation-state`
  → `fix/live-run-findings` → `refactor/tools-return-data` →
  `fix/clock-and-dead-data` (nhánh hiện tại). Sơ đồ đầy đủ ở `CONTEXT.md` mục
  Trạng thái. Cách gộp là quyết định của chủ dự án.
- **PR #6 đang mở**: `henry/develop` → `main`, 85 commit / 13 merge.
  `main` chỉ nhận qua PR này.
- Nhật ký từng đợt, kết quả đo và các quan sát còn để ngỏ: nửa sau
  [`CONTEXT.md`](CONTEXT.md) ("Nhật ký các đợt việc").

**Quy ước nhánh:** mỗi việc một nhánh riêng → merge vào `henry/develop` →
`main` chỉ nhận bằng cách merge `henry/develop`. Không commit thẳng lên `main`.

## Việc tiếp theo

1. **BUG `is_affirmative` — làm trước hết.** Một câu tán gẫu có chứa "phải",
   "được", "dạ" hay "đúng" bị coi là ĐỒNG Ý, và node `confirm` **ghi lịch thật**
   cho khách. Đã tái hiện hai lần. Chi tiết, cách tái hiện, và vì sao không sửa
   nhanh được: mục "Việc còn dở" đầu tiên của [`CONTEXT.md`](CONTEXT.md) và mục
   "Để sau" bên dưới. Nặng hơn mọi thứ bốn nhánh đang chờ merge sửa được.
2. **Quyết cách gộp bốn nhánh xếp chồng** (sơ đồ ở `CONTEXT.md` mục Trạng thái)
   và **merge PR #6 / PR #7** khi thấy sẵn sàng.
3. Hai mục **P1** trong `REPO_AUDIT.md` (bên dưới) — cần xong trước khi mở cho
   khách thật. Chủ dự án đã chốt **để tới lúc deploy mới làm**.
4. Quan sát đã đo nhưng **chưa quyết** (chi tiết ở "Việc còn dở" của
   `CONTEXT.md`): đo token `ConversationState` khi Langfuse sống; hai lần từ chối
   liên tiếp ra câu gần y nhau; câu hỏi xác nhận bị tính là lặp; neo có thể ra
   giờ đã qua; `slots.declined` sót mốc; câu mời lặp gần y hệt.

**Đã chốt là KHÔNG sửa bây giờ:** AI tự ước lượng thời lượng dịch vụ ("khoảng
1 tiếng rưỡi đến 2 tiếng") — chủ dự án chấp nhận vì đó là ước lượng hợp lý,
dù tiệm không có dữ liệu thời lượng nào.

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

- [ ] **BUG — `is_affirmative()` khớp "phải"/"được"/"dạ"/"đúng" bất kỳ đâu
      trong câu.** `is_affirmative()` (`app/agents/booking_graph/confirm.py:22-26`)
      khớp các chữ đó ở BẤT KỲ đâu trong câu khách gõ, nên một câu tán gẫu có
      chứa "phải" (ví dụ: "...việc gì cũng phải tính trước cho chắc") bị coi
      là ĐỒNG Ý, và node `confirm` **ghi lịch thật** ngay giữa cuộc, dù khách
      không hề chốt. Đã tái hiện thật: câu trên đặt lịch thành công. Bug này
      đã làm hỏng **hai lần nghiệm thu liên tiếp** (LLM-32 chạy trước đợt này
      và lần chạy trong đợt này) mà không ai nhận ra lúc đó — xem đoạn sửa lại
      kết luận ở "Đang ở đâu" phía trên. Sửa cần cẩn thận: bỏ "phải" khỏi
      `_YES` sẽ làm "phải rồi" không còn được hiểu là đồng ý — cần tách theo
      cụm từ, không theo từ đơn. **Không sửa `confirm.py` trong đợt final-fix
      2026-09-20** — ngoài phạm vi, cần thiết kế riêng.
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
