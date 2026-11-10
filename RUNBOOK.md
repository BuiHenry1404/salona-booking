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
PYTHONPATH=. .venv/bin/python scripts/seed_services.py
```

`requirements.txt` là nguồn phụ thuộc thật. `bcrypt` ghim ở **4.0.1**: passlib 1.7.4 hỏng với bcrypt 5.x,
đừng nâng.

`scripts/seed_services.py` nạp danh mục dịch vụ (collection `services`, mock —
file Excel thật chủ dự án gửi sau) cho agent **Dịch vụ & giá** và cho
`ProposalService` tính thời lượng lịch theo dịch vụ. Idempotent (upsert theo
`id`), chạy lại an toàn; dữ liệu sai (vd. `duration_minutes` không phải bội số
15) thoát mã khác 0 và KHÔNG ghi gì.

## 3. Cấu hình

**Cấu hình chỉ đọc từ `.env`.** Biến môi trường của shell bị bỏ qua hoàn toàn
(`Settings.settings_customise_sources`). Export biến ở shell rồi tưởng nó có tác
dụng là hiểu nhầm phổ biến nhất ở đây.

`config.py` không có giá trị mặc định nào — thiếu một khoá bắt buộc thì app không
khởi động, kèm tên trường bị thiếu. Thêm thiết lập mới phải sửa cả `config.py`,
`.env.example` và `.env`.

### Mongo: replica set một node (bắt buộc)

`decide_pending` ghi lịch và `turn_ledger` trong **một transaction Mongo**
(Task 3.6) — transaction chỉ chạy trên replica set, standalone sẽ lỗi
`Transaction numbers are only allowed on a replica set member or mongos`.
Compose chạy `mongod --replSet rs0`; healthcheck tự `rs.initiate` lần đầu (đã
khởi tạo thì bỏ qua) với thành viên `mongo:27017`, healthy = đã là PRIMARY.

Chuỗi kết nối **phải** kèm `directConnection=true` — máy host không phân giải
được tên `mongo`, thiếu tham số này thì driver đi khám phá thành viên rồi treo
tới `ServerSelectionTimeoutError` ("Could not reach any servers in [('mongo', 27017)]"):

| Nơi | `MONGO_URI` |
|---|---|
| `.env` (máy host: uvicorn dev, script) | `mongodb://localhost:27017/?directConnection=true` |
| `.env.docker` (container `app`) | `mongodb://mongo:27017/?directConnection=true` |
| `tests/conftest.py` | `TEST_MONGO_URI` đã kèm sẵn |

Máy đang có container Mongo standalone cũ: `docker compose up -d --wait mongo`
tạo lại container với cấu hình mới (volume `mongo_data` giữ nguyên dữ liệu),
rồi kiểm: `docker compose exec mongo mongosh --quiet --eval "rs.status().ok"` → `1`.

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

Chat của khách chạy qua gói `app/agents/salon/` (`run_turn` / `ButtonReply`,
`app/services/socketio_service.py`). Không còn sự kiện `token`: mọi câu tới
khách chỉ qua `complete`, sau khi qua guard bằng code.

Mở http://localhost:8000/static/socketio_test.html, dán `access_token`, rồi trong
console:

```js
say("mai 3h chiều làm tóc được không")   // hỏi lại xác nhận, CHƯA đặt lịch
say("ừ")                                 // giờ mới ghi lịch
say("chị bán bảo hiểm không")            // bị từ chối lịch sự
```

Đúng thì lượt 1 phát `turn_started` → `tool_started`/`tool_finished` (mỗi sự
kiện kèm `name` kỹ thuật + `label` tiếng Việt cho khách, vd
`{"name": "appointment_task", "label": "Đang xem lịch hẹn…"}`) → đúng MỘT
`complete {"answer", "proposal"}` (`proposal` khác `None` khi có đề xuất đang
chờ: `{"id", "kind", "expires_at", "when_spoken"}` để frontend hiện nút Đồng
ý/Không) — mà `GET /api/v1/appointments/mine` vẫn rỗng. Đề xuất nằm ở
collection `proposals` (`status` pending → approved/rejected/expired/
superseded/failed); `salon/` không dùng `interrupt()` — hai lượt chat vẫn là
hai turn độc lập, phiên chờ nằm ở Mongo (`waiting_sessions`), không ở state
graph (docs/architech-agent.md luật 7).

Lượt 2 (gõ "ừ") không có tool nào: Decision Gate (`veto`) đọc nhãn
`decide_pending` của Orchestrator, `apply_decision` ghi lịch qua
`ProposalService.approve`, Orchestrator viết câu chốt — qua guard rồi mới tới
khách. Bấm nút Đồng ý/Không trên frontend đi bằng sự kiện `confirm_proposal`
(không phải `send_message`): đường này là API bằng code, KHÔNG gọi model
(docs/architech-agent.md luật 3) — nhanh và không tốn lời gọi LLM.

## 7. Test

```bash
docker compose up -d mongo
.venv/bin/python -m pytest -q
```

Cần Mongo ở `localhost:27017`, chạy replica set (mục 3); test dùng database riêng
`chatbot_test_db` và tự dọn sau mỗi test.

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

Kịch bản 10 lượt riêng cho gói `app/agents/salon/` (Task 18–19 của đợt dựng lại
agent) — tự xoá sạch dữ liệu, seed user + danh mục dịch vụ, tạo sẵn một lịch
chặn 15:00 ngày mai rồi gọi thẳng `run_turn`/`ButtonReply` (không qua HTTP),
in câu bot, nguồn câu (llm/code, đọc từ `salon_turns`), thời gian chạy và
trạng thái Mongo sau mỗi lượt. Gọi Azure thật — **hỏi chủ dự án trước khi
chạy** (CONTEXT.md quyết định 7):

```bash
docker compose up -d mongo
PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py
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

## 8b. Kiểm phiên chờ và cuộc trò chuyện (gói `salon/`)

Gói mới không còn `ConversationState` (`summary`/`slots` nén theo ngưỡng
token) — việc dở của agent con qua lượt nằm ở collection Mongo
`waiting_sessions` (qua `WaitingSessionService`), KHÔNG trong state của
graph; lịch sử chat dài được nén bằng `SummarizationMiddleware` của
LangChain (ngắn hạn, trong checkpointer theo thread), không phải một bước
nén riêng của dự án.

Xem phiên chờ đang mở của một khách (mỗi khách tối đa một phiên mỗi agent
con — Đặt lịch / Thông tin tiệm / Dịch vụ & giá — hết hạn
`salon_session_ttl_minutes` phút tính từ câu hỏi gần nhất, kiểm bằng code lúc
đọc, không phải TTL Mongo):

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'var u=db.users.findOne({phone:"<SĐT>"}); db.waiting_sessions.find({user_id:String(u._id), status:"waiting"}).forEach(printjson)'
```

Xem cuộc trò chuyện hiện tại (`conversation_threads`, một cuộc mỗi ngày VN —
mục Chốt cứng "Phiên hội thoại" ở `CONTEXT.md`):

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'var u=db.users.findOne({phone:"<SĐT>"}); db.conversation_threads.find({user_id:String(u._id)}).sort({created_at:-1}).limit(3).forEach(printjson)'
```

Ledger một lượt (`salon_turns`, gói mới — khác `turn_ledger` của gói cũ đã
xoá): mỗi lượt một bản ghi, có `facts` agent con trích, `decision` (nếu lượt
có duyệt/từ chối đề xuất), `ended_by`, `answer_source` (`llm`/`code`):

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'var u=db.users.findOne({phone:"<SĐT>"}); db.salon_turns.find({user_id:String(u._id)}).sort({asked_at:-1}).limit(5).forEach(printjson)'
```

Bộ nhớ dài hạn (`MongoDBStore`, namespace `("memories", user_id)`, 3 mục cố
định `preferences`/`notes`/`last_visit`, tổng tối đa
`salon_memory_max_chars` ký tự) nằm trong collection của thư viện
`langgraph-store-mongodb` (mặc định `store`); gom lại ở lượt đầu của một
cuộc trò chuyện mới, log `memory_consolidate_failed` nếu lỗi/quá giờ (lượt
vẫn chạy tiếp với bộ nhớ cũ).

Log cần theo dõi (qua `structlog`, không ném lỗi ra khách):

- `turn_started` / `turn_finished` — mỗi lượt, kèm `user_id`/`turn_id`/
  `thread_id`/`kind` (`typed`/`button`), `ended_by`, `answer_source`.
- `turn_superseded` — khách nhắn lượt mới khi lượt cũ còn chạy (thường do
  Azure treo) — lượt cũ bị huỷ êm, phiên nó mở bị đóng, không phát `error`.
- `sessions_closed` — phiên chờ bị đóng trong lượt (quyết định xong, đổi
  sang việc khác, hoặc lượt bị thay/lỗi), kèm `agents`/`reason`.
- `memory_consolidate_failed` — bước gom bộ nhớ dài hạn lỗi/quá giờ
  (`salon_memory_timeout_seconds`); không chặn lượt chat.

## 8b'. Checkpointer (lịch sử model theo cuộc trò chuyện)

Lịch sử model nằm trong hai collection của chính DB `MONGO_DB_NAME`
(không phải DB `checkpointing_db` mặc định của thư viện):

- `checkpoints` — state graph sau mỗi bước, theo `thread_id = conversation_id`
  (id của bản ghi trong `conversation_threads` — một cuộc mỗi ngày VN).
- `checkpoint_writes` — ghi tạm giữa các bước.

Cả hai có index TTL trên `created_at`: tự xoá sau `CHECKPOINT_TTL_DAYS` ngày
(mặc định 7, đặt trong `.env`). Đổi TTL rồi khởi động lại là đủ: lúc khởi động
`make_checkpointer` sửa index TTL đang có bằng `collMod` (không xoá, không tạo
lại index; thư viện tự làm thì Mongo ném "equivalent index already exists with
different options" và app không lên). Xoá checkpoint chỉ mất lịch sử model
của cuộc đó — nhật ký chat hiển thị nằm ở `conversations`, không bị chạm;
checkpoint KHÔNG phải nguồn sự thật cho lịch/đề xuất/phiên chờ (CONTEXT.md
mục Chốt cứng "Checkpoint").

Xem các cuộc trò chuyện gần đây của một khách:

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'var u=db.users.findOne({phone:"<SĐT>"}); var ids=db.conversation_threads.find({user_id:String(u._id)}).sort({created_at:-1}).limit(3).toArray().map(t=>String(t._id)); db.checkpoints.find({thread_id:{$in:ids}},{thread_id:1,checkpoint_id:1}).sort({checkpoint_id:-1}).limit(5).forEach(printjson)'
```

Dọn tay (vd. sau khi đổi cấu trúc state, hoặc reset dev):

```bash
# Một cuộc trò chuyện
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'db.checkpoints.deleteMany({thread_id:"<conversation_id>"}); db.checkpoint_writes.deleteMany({thread_id:"<conversation_id>"})'
# Toàn bộ
docker compose exec -T mongo mongosh salon_booking --quiet --eval \
  'db.checkpoints.deleteMany({}); db.checkpoint_writes.deleteMany({})'
```

## 8c. Kiểm tầng gác (guard của gói `salon/`)

Câu trả lời cuối của Orchestrator (AIMessage không tool call) đi qua
`ReplyGuardMiddleware.aafter_model` (`app/agents/salon/orchestrator/middleware/
reply_guard.py`) ĐÚNG MỘT LẦN trước khi tới khách — luật kiểm thuần code,
không LLM giám khảo, ở `app/agents/salon/guard/checks.py::check_reply`
(docs/architech-agent.md luật 4–5). Mười một mã lỗi theo thứ tự cố định
(`CHECK_CODES`): `empty`, `claim` (nhận vơ đã đặt/dời/huỷ/giữ không có thật),
`unknown_time`, `unknown_price`, `missing_fact`, `missing_question`,
`missing_phone`, `phone_not_allowed`, `pending_undecided` (đề xuất đang chờ
chưa được quyết lượt này), `pronoun` (mục Xưng hô), `repeat` (giống câu đáp
gần đây — `REPEAT_RATIO`/`REPEAT_MIN_WORDS`/`REPEAT_LOOKBACK` ở `checks.py`).
Log qua `structlog`, không ném lỗi ra khách:

- `guard_rejected{codes}` — câu đầu tiên phạm một hay nhiều mã. Câu bị XOÁ
  khỏi state (model không thấy lại câu sai của chính nó), guard thêm một
  `HumanMessage` tiếng Anh nêu từng mã + điều cần sửa
  (`guard_hint`/`_CODE_HINTS`), `jump_to: "model"` — Orchestrator viết lại
  ĐÚNG MỘT LẦN, KHÔNG được gọi tool (trừ `decide_pending` nếu lý do là
  `pending_undecided`).
- `guard_violation{codes, closed_sessions}` — bản viết lại VẪN phạm. Guard
  đóng mọi phiên chờ lượt này vừa mở (giữ phiên mà câu mẫu dưới đây hỏi lại,
  Ruling R12) rồi trả câu mẫu CODE (`fallback_reply`, `salon_source: "code"`),
  `jump_to: "end"` — không còn lần viết lại thứ hai.
- Không log gì khi câu đạt ngay (chỉ xoá tên tool/khối JSON lọt vào câu bằng
  `strip_tool_text` nếu có).

Guard chỉ xét DỮ KIỆN LƯỢT (`build_reply_facts`, `app/agents/salon/guard/
facts.py`) dựng từ bối cảnh (DB đọc lại) + `turn_facts` agent con trích từ
ToolMessage + quyết định đề xuất — không tin chữ model. Mã `bad_tool_call`
cũng qua đường `guard_rejected`/`guard_violation` khi AIMessage có tool call
hỏng (`invalid_tool_calls`).

Lỗi trần/ngân sách/timeout ở tầng cao hơn (hết `salon_model_calls_per_turn`,
`salon_tool_calls_per_turn`, `orch_llm_calls_per_user_per_hour`,
`orch_orchestrate_timeout_seconds`) không qua guard — `run_turn` bắt lỗi,
đóng phiên chờ mở trong lượt, viết một câu mẫu code từ ledger (`fallback_reply`
hoặc `TURN_ERROR_MESSAGE` nếu lượt chưa làm được gì), log
`orchestrator_timeout`/`orchestrator_limit`/`orchestrator_failed`.

## 9. Trục trặc hay gặp

| Hiện tượng | Nguyên nhân |
|---|---|
| Sửa `.env` không thấy tác dụng | Đã đọc mục 3 chưa — hay bạn đang sửa nhầm `.env.docker`? |
| Chat trả `error`, app vẫn chạy bình thường | Thiếu/sai cấu hình Azure. Xem log lúc khởi động, nó chỉ *cảnh báo* rồi chạy tiếp |
| Trace không lên Langfuse dù key đúng | Thiếu gói `langchain` (khác `langchain-core`) — `pip install -r requirements.txt` lại |
| Mọi trace hiện 0đ | Chưa seed đơn giá model, xem mục 8. Đơn giá chỉ áp cho trace **mới** |
| Test đỏ hàng loạt với `ServerSelectionTimeoutError` | Mongo chưa chạy, hoặc mongod chết vì hết file descriptor (compose đã set `nofile` 64000) |
| `307` khi gọi health | Thiếu dấu `/` cuối |
| `ServerSelectionTimeoutError` nhắc tới `('mongo', 27017)` | `MONGO_URI` thiếu `?directConnection=true` (mục 3) |
| `Transaction numbers are only allowed on a replica set member` | Container Mongo còn là standalone cũ — `docker compose up -d --wait mongo` |
