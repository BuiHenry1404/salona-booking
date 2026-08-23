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

`scripts/seed_dev_users.py` tạo sẵn hai tài khoản (chạy lại nhiều lần được, đã có
thì bỏ qua). **Không có đăng ký tự do** — muốn thêm khách thì admin gọi
`POST /api/v1/auth/users`.

| Vai trò | SĐT | Mật khẩu | Tên | Dùng để |
|---|---|---|---|---|
| admin | `0901234567` | `chutiem123` | Chủ tiệm | bật/tắt bận rảnh, xem lịch mọi khách, tạo tài khoản |
| khách | `0912345678` | `matkhau123` | Cô Lan | chat đặt lịch, xem lịch của mình |

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

## 9. Trục trặc hay gặp

| Hiện tượng | Nguyên nhân |
|---|---|
| Sửa `.env` không thấy tác dụng | Đã đọc mục 3 chưa — hay bạn đang sửa nhầm `.env.docker`? |
| Chat trả `error`, app vẫn chạy bình thường | Thiếu/sai cấu hình Azure. Xem log lúc khởi động, nó chỉ *cảnh báo* rồi chạy tiếp |
| Trace không lên Langfuse dù key đúng | Thiếu gói `langchain` (khác `langchain-core`) — `pip install -r requirements.txt` lại |
| Mọi trace hiện 0đ | Chưa seed đơn giá model, xem mục 8. Đơn giá chỉ áp cho trace **mới** |
| Test đỏ hàng loạt với `ServerSelectionTimeoutError` | Mongo chưa chạy, hoặc mongod chết vì hết file descriptor (compose đã set `nofile` 64000) |
| `307` khi gọi health | Thiếu dấu `/` cuối |
