# Runbook — chạy và dùng dự án

Sổ tay thao tác cho người mới vào máy: dựng môi trường, chạy app, đăng nhập bằng
tài khoản test, xem trace. Phần *tại sao* nằm ở [`CONTEXT.md`](CONTEXT.md); file
này chỉ trả lời *gõ gì*.

## 1. Yêu cầu

- Python 3.12 (`.venv` trong repo dựng bằng [uv](https://docs.astral.sh/uv/))
- Docker + Docker Compose
- Một Azure OpenAI deployment (không có thì app vẫn chạy, chỉ phần chat AI hỏng)

## 2. Dựng lần đầu

```bash
uv venv                                  # nếu chưa có .venv
uv pip install -r requirements.txt
cp .env.example .env                     # rồi điền, xem mục 3
docker compose up -d mongo
PYTHONPATH=. .venv/bin/python scripts/seed_dev_users.py
```

## 3. Cấu hình

**Cấu hình chỉ đọc từ `.env`.** Biến môi trường của shell bị bỏ qua hoàn toàn
(`Settings.settings_customise_sources`). Export biến ở shell rồi tưởng nó có tác
dụng là hiểu nhầm phổ biến nhất ở đây.

`config.py` không có giá trị mặc định nào — thiếu một khoá bắt buộc thì app không
khởi động, kèm tên trường bị thiếu. Thêm thiết lập mới phải sửa cả `config.py`,
`.env.example` và `.env`.

Bốn khoá Azure hay điền sai:

| Khoá | Điền gì | Sai thì |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | URL gốc của resource, **không** kèm `/openai/v1` | 404 Resource not found |
| `AZURE_OPENAI_DEPLOYMENT` | Tên **deployment**, không phải tên model | 404 DeploymentNotFound |
| `AZURE_OPENAI_API_KEY` | Key của resource | mọi lượt chat trả sự kiện `error` |
| `AZURE_OPENAI_API_VERSION` | ví dụ `2025-04-01-preview` | 404 hoặc lỗi schema |

### Provider khác: OpenAI-compatible

Không muốn dùng Azure thì đặt `LLM_PROVIDER=openai_compatible` và điền ba biến
`OPENAI_COMPATIBLE_BASE_URL` / `OPENAI_COMPATIBLE_API_KEY` / `OPENAI_COMPATIBLE_MODEL`.
Endpoint là bất kỳ dịch vụ nào nói giao thức OpenAI: gateway tự dựng, nhà cung
cấp khác, hoặc gateway chạy local. Chat, tool calling và streaming đều đi qua đó.
Thiếu biến nào thì log khởi động nêu đúng **tên biến** đó (không in giá trị).
URL/key/model chỉ nằm trong `.env`, không hardcode trong code.

Langfuse và Telegram: **comment cả dòng** thì tắt. Để `KEY=` trống *không* tắt —
chuỗi rỗng khác `None`.

## 4. Chạy app

```bash
docker compose up -d mongo
.venv/bin/python -m uvicorn main:app --reload
```

- Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health/ — **nhớ dấu `/` cuối**, thiếu là 307
- Trang test chat: http://localhost:8000/static/socketio_test.html

Chạy cả trong Docker: `docker compose up -d`. Container đọc `.env` cộng thêm
`.env.docker` (mount vào `/run/config/env.docker`) để đổi host Mongo. Khối
`environment:` trong compose **không** có tác dụng — Settings không đọc biến môi
trường.

**Production: đúng 1 worker.** Không `--workers`, không `gunicorn -w N`, không
scale nhiều replica — bot Telegram (Plan 3) dùng long polling và Telegram chỉ cho
một kết nối `getUpdates` mỗi token.

## 5. Tài khoản test

`scripts/seed_dev_users.py` tạo sẵn năm tài khoản (chạy lại nhiều lần được, đã có
thì bỏ qua). **Không có đăng ký tự do** — muốn thêm khách thì admin gọi
`POST /api/v1/auth/users`.

| Vai trò | SĐT | Mật khẩu | Tên | Dùng để |
|---|---|---|---|---|
| admin | `0901234567` | `chutiem123` | Chủ tiệm | bật/tắt bận rảnh, xem lịch mọi khách, tạo tài khoản |
| khách | `0912345678` | `matkhau123` | Cô Lan | chat đặt lịch, xem lịch của mình |
| khách | `0923456789` | `matkhau123` | Chú Hùng | " |
| khách | `0934567890` | `matkhau123` | Bác Ba | " |
| khách | `0945678901` | `matkhau123` | Cô Tám | " |

Bốn khách để thử những thứ cần nhiều người cùng lúc: giành slot (hai người đặt
trùng giờ → 409), hạn mức chat tính riêng từng người, và thông báo realtime chỉ
về đúng socket của chủ nhân.

Lấy token:

```bash
curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"0912345678","password":"matkhau123"}'
```

Trả về `access_token` (30 phút). Refresh token **không** nằm trong body — nó đi
bằng cookie HttpOnly, nên muốn thử `/auth/refresh` phải dùng `curl -c/-b` hoặc
trình duyệt.

Sai mật khẩu 10 lần trong 15 phút là khoá tạm theo SĐT và IP — lúc đó mật khẩu
đúng cũng bị từ chối, đợi hết cửa sổ hoặc xoá collection `rate_limits`.

## 6. Thử chat

Mở http://localhost:8000/static/socketio_test.html, dán `access_token`, rồi trong
console:

```js
say("mai 3h chiều làm tóc được không con")   // hỏi lại xác nhận, CHƯA đặt lịch
say("ừ")                                      // giờ mới ghi lịch
say("cháu bán bảo hiểm không")                // bị từ chối lịch sự
```

Đúng thì lượt 1 hiện `tool_started parse_time` → `propose_appointment` → nhiều
`token` → `complete`, mà `GET /api/v1/appointments/mine` vẫn rỗng. Lượt 2 không có
tool nào, trả lời gần như tức thì (đi nhánh `confirm`, không gọi LLM), và lịch xuất
hiện.

## 7. Test

```bash
docker compose up -d mongo
.venv/bin/python -m pytest -q
```

Cần Mongo ở `localhost:27017`; test dùng database riêng `chatbot_test_db` và tự dọn
sau mỗi test.

Kiểm đầu-cuối trên server đang chạy — HTTP + Socket.IO thật, gọi Azure thật,
không mock gì (31 case: xác thực, phân quyền, bận/rảnh, đặt/huỷ lịch, hai lượt
chat, lịch sử trò chuyện):

```bash
docker compose up -d
PYTHONPATH=. .venv/bin/python scripts/live_e2e.py
```

Diễn lại tám cuộc trò chuyện thật của khách (đặt lịch suôn sẻ, nói mơ hồ, hỏi
bận/rảnh, trùng giờ, đổi ý, hủy lịch, câu ngoài chủ đề, giờ đóng cửa) — in ra
nguyên văn hội thoại kèm tool đã gọi:

```bash
PYTHONPATH=. .venv/bin/python scripts/chat_scenarios.py
```

Nhóm test gọi Azure thật bị loại khỏi lần chạy mặc định (`-m "not llm"` trong
`pyproject.toml`). Chạy tay khi sửa prompt của parser thời gian:

```bash
.venv/bin/python -m pytest tests/test_timeparse_llm.py -m llm -v
```

## 8. Langfuse (tuỳ chọn)

```bash
docker compose -f docker-compose.langfuse.yml up -d
```

- UI: http://localhost:3100 — `dev@salon.local` / `langfuse123`
- Key project tạo sẵn: `pk-lf-salon-dev` / `sk-lf-salon-dev`, điền vào `.env`
- Xem tool đã gọi: **Tracing → Traces →** mở trace, các dòng nhãn `TOOL` là tool
  call, bấm vào xem input/output. Hoặc tab **Observations**, lọc `Type = TOOL`.
- Chi phí chỉ hiện khi có model definition khớp tên model. Dựng lại stack từ đầu
  thì seed lại đơn giá bằng lệnh `curl` ghi ở đầu `docker-compose.langfuse.yml`.

Tắt: `docker compose -f docker-compose.langfuse.yml down` (stack này nặng: Postgres
+ ClickHouse + Redis + MinIO + web + worker).

## 8b. Kiểm state (nén hội thoại trong phiên)

Xem `ConversationState` (`summary` + `slots`) hiện có của một khách:

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'var u=db.users.findOne({phone:"<SĐT>"}); printjson(db.conversations.findOne({user_id:String(u._id)},{state:1}))'
```

Log cần theo dõi (cả năm đều log qua `structlog`, không ném lỗi ra khách):

- `state_compacted` — nén thành công, kèm số bullet, số tin đã nén, và có
  sinh được slots hay không.
- `state_skipped` — cầu chì đã bật (3 lần hỏng liên tiếp), bỏ qua tới hết ngày.
- `state_empty` — model trả về 0 bullet hợp lệ; coi như hỏng, `covers_until`
  KHÔNG advance, state cũ (nếu có) giữ nguyên, `failures` tăng.
- `state_llm_failed` — lời gọi LLM lỗi hoặc timeout; `failures` tăng (bump cầu
  chì), state cũ giữ nguyên.
- `state_failed` — lỗi ngoài dự kiến, NGOÀI lời gọi LLM (vd. Mongo down);
  không bump cầu chì.
- `state_schedule_failed` — lên lịch nén nền thất bại sau khi lượt chat đã
  trả lời khách xong; kèm `error_type`.
- `state_slot_dropped` — **KHÔNG phải lỗi.** `sanitize_slots` bỏ đúng một
  field slots do LLM gõ sai (vd. ngày không hợp lệ, ngày/giờ đã qua, id lịch
  không có thật) — kèm tên field bị bỏ. Thấy log này là code đang lọc đúng
  việc, không phải hệ thống hỏng.

Trace Langfuse của lượt nén mang tag `state` (khác tag `respond` của lượt
chat chính) — lọc theo tag đó để tách chi phí nén khỏi chi phí trả lời.

Ngưỡng nén mặc định `COMPACT_THRESHOLD_TOKENS = 800`
(`app/services/conversation_state.py`) — kịch bản `dai` (16 lượt, câu ngắn)
không luôn đủ để kích nén; muốn xác nhận luồng đầu-cuối nhanh thì hạ tạm
ngưỡng (vd. 300), chạy lại, rồi **khôi phục về 800** trước khi commit bất cứ
gì.

## 8c. Kiểm tầng gác (guard/rewrite/phrase)

Mọi câu LLM đi qua node `guard` đúng một lần trước khi ra khách (spec
`docs/superpowers/specs/2026-09-14-natural-voice-guard-design.md`; xem bẫy #21
ở `CONTEXT.md`). Log qua `structlog`, không ném lỗi ra khách:

- `guard_violation{codes}` — draft đầu tiên phạm một hay nhiều trong ba phép
  kiểm tất định: `repeat` (giống câu đáp gần đây, xem `REPEAT_RATIO`/
  `REPEAT_LOOKBACK` ở `guard.py`), `pronoun` (giọng "cô/chú/bác" hoặc đại từ
  sai giới với `address` suy từ tên khách), `clock` (giờ viết bằng chữ hoặc
  `HH:MM` thay vì "3 giờ chiều"). Sang node `rewrite`.
- `guard_rewritten` — bản viết lại (LLM, tag `rewrite`, một lần, không stream)
  qua hết mọi phép kiểm lại (cộng `content` — còn đủ mốc ngày-giờ/số của bản
  gốc). Đây là câu được phát.
- `guard_gave_up{codes}` — bản viết lại VẪN phạm (kể cả mất số liệu qua
  `content_kept`). Guard trả **draft GỐC** (`answer_source="llm"`), không phát
  bản rewrite hỏng — không có lần rewrite thứ hai.
- `rewrite_failed{error,codes}` (ở `rewrite.py`) — lời gọi LLM viết lại lỗi
  hoặc quá `REWRITE_TIMEOUT_SECONDS` (8s); node trả nguyên draft, guard sẽ thấy
  lại đúng các `codes` cũ và thành `guard_gave_up`.
- `phrase_fallback{codes}` — nhánh riêng cho câu chốt lịch sau `confirm`
  (`_guard_phrase` trong `guard.py`): câu do `phrase` node viết thiếu `when`
  (mốc ngày-giờ từ DB), thiếu cách gọi khách (`address`), thiếu câu báo lỗi khi
  đặt hỏng, hoặc phạm ba phép kiểm thường — dùng ngay câu cứng
  `state["fallback"]`, không rewrite (đây là khoảnh khắc chốt lịch, sai số liệu
  không được phép).
- `phrase_failed{error}` (ở `phrase.py`) — lời gọi LLM của node `phrase` lỗi
  hoặc quá `PHRASE_TIMEOUT_SECONDS` (8s); dùng thẳng `fallback`.
- `anchor_ignored{anchor}` (ở `tools.py`) — tool gọi `apply_anchor` với neo
  không hợp lệ (không parse được thành `datetime`), bỏ qua neo, hỏi lại khách
  như trước khi có Task 3.
- `anchor_bad_hour{partial_hour,partial_minute}` (ở `timeparse.py`) — LLM trả
  `partial_hour`/`partial_minute` ngoài khoảng hợp lệ (không bị Pydantic chặn
  vì chỉ là `int`); bỏ qua neo, trả nguyên candidate để khách vẫn được hỏi lại
  thay vì tool ném `ValueError`.

Đo thật 2026-09-14 (Task 6, 26 lượt LLM qua hai kịch bản `tu_nhien`+`dai`):
đúng **1** `guard_violation` (`repeat`) → **1** `guard_gave_up` — rewrite chạy
nhưng bản viết lại vẫn lặp nên bị guard trả về draft gốc; không log nào khác
trong danh sách trên xuất hiện. Xem chi tiết ở Checkpoint Task 6 cuối
`CONTEXT.md`.

Kiểm bằng tay xem draft nào bị cờ `repeat` oan hay đúng (không gọi LLM, đọc
transcript có sẵn):

```bash
PYTHONPATH=. .venv/bin/python scripts/probe_repeats.py [--ratio 0.85] [--min-words 6] FILE...
```

Mỗi file truyền vào phải đúng định dạng dòng `BOT   : ...` của
`scripts/chat_e2e_transcript.py`; không truyền file nào thì nó tự nhặt hết
`*.txt` ở gốc repo.

## 9. Trục trặc hay gặp

| Hiện tượng | Nguyên nhân |
|---|---|
| Sửa `.env` không thấy tác dụng | Đã đọc mục 3 chưa — hay bạn đang sửa nhầm `.env.docker`? |
| Chat trả `error`, app vẫn chạy bình thường | Thiếu/sai cấu hình Azure. Xem log lúc khởi động, nó chỉ *cảnh báo* rồi chạy tiếp |
| Trace không lên Langfuse dù key đúng | Thiếu gói `langchain` (khác `langchain-core`) — `pip install -r requirements.txt` lại |
| Mọi trace hiện 0đ | Chưa seed đơn giá model, xem mục 8. Đơn giá chỉ áp cho trace **mới** |
| Test đỏ hàng loạt với `ServerSelectionTimeoutError` | Mongo chưa chạy, hoặc mongod chết vì hết file descriptor (compose đã set `nofile` 64000) |
| `307` khi gọi health | Thiếu dấu `/` cuối |
