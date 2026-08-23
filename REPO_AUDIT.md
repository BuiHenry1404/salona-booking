# Full Repository Audit

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](CONTEXT.md).

> Ghi chú về model: yêu cầu ghi "dùng DeepSeek V4 Pro". Phiên chạy audit này là
> opencode với model được cấu hình sẵn (9router/open-code) — không tự đổi model
> được. Toàn bộ kết luận dưới đây dựa trên đọc nguồn trực tiếp, không suy đoán.

## Executive Summary

Repo ở trạng thái **tốt, an toàn để tiếp tục phát triển/review**. Kiến trúc có kỷ
luật cao: mọi nghiệp vụ nằm trong `app/services/`, tool agent không có quyền ghi
lịch trực tiếp, auth dùng refresh token HttpOnly + access token chỉ trong bộ nhớ,
Socket.IO xác thực JWT, Telegram whitelist chat_id. Test phủ dày (backend 281 test
xanh theo AGENTS.md; frontend 251 test + build pass, đã xác minh ở phiên trước).

**Không có lỗi CRITICAL/HIGH nào được xác nhận.** Có một số vấn đề MEDIUM cần xử
lý trước khi mở cho khách thật, phần còn lại là LOW/INFO.

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 0 |
| MEDIUM   | 6 |
| LOW      | 6 |
| INFO     | 2 |
| **Total**| **14** |

**Top 5 ưu tiên:**

1. SEC-01 — Socket.IO KHÔNG kiểm `token_version`: đổi mật khẩu thu hồi token trên
   HTTP nhưng socket cũ vẫn sống tới hết hạn JWT.
2. SEC-02 — Không có rate limit / tuần tự hóa cho `send_message`: một khách spam
   sẽ kéo chi phí LLM và nghẽn bot Telegram long-polling (1 worker).
3. REL-02 — `ShopHoursRequest.open_time/close_time` không validate định dạng;
   chuỗi sai làm `_to_minutes` ném ValueError → hỏng mọi lượt đặt lịch.
4. SEC-03 — `docker-compose.yml` expose Mongo 27017 không auth + mongo-express
   admin/admin (đã ghi chú dev-only, chưa tách prod) — chặn triển khai ra ngoài.
5. DEP-01 — `python-jose` đã bị bỏ hoang (CVE đã công bố), `passlib` ngừng bảo
   trì, nhiều dependency pin cũ.

## Repository Snapshot

- **Branch:** `feat/openai-compatible-llm`
- **HEAD:** `3fe1a25` — "feat: add portable LLM provider and fix chat streaming"
- **Working tree lúc audit:** chỉ `?? opencode.json` (local-only, đúng dự kiến).

**Kiến trúc:**

- Backend: FastAPI + Motor/MongoDB (1 worker bắt buộc — Telegram long polling),
  LangGraph booking agent (supervisor → booking/status/refuse/confirm), Socket.IO
  ASGI mount tại `/socket.io`, Telegram bot tự viết bằng httpx (không framework),
  Langfuse tracing, structlog JSON.
- Frontend: React 18 + Vite + socket.io-client, StrictMode, không localStorage
  cho token, UI tiếng Việt cho khách lớn tuổi.
- Config chỉ đọc từ `.env` + `/run/config/env.docker` (bỏ qua biến shell) —
  `app/core/config.py:100-117`.

## Findings Summary

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| SEC-01 | MEDIUM | Socket.IO auth | Không kiểm `token_version` khi connect | CONFIRMED |
| SEC-02 | MEDIUM | Socket.IO / LLM | `send_message` không rate limit, không tuần tự | CONFIRMED (code) |
| SEC-03 | MEDIUM | Deploy | Mongo không auth + mongo-express admin/admin | CONFIRMED (dev-only) |
| SEC-04 | LOW | CORS | `ALLOWED_ORIGINS` + credentials thiếu guard cho wildcard | LIKELY (cần cấu hình sai) |
| REL-01 | MEDIUM | Concurrency | Hai lượt chat song song cùng user giẫm pending/stream | LIKELY |
| REL-02 | MEDIUM | Validation | Giờ mở cửa không validate → ValueError dây chuyền | CONFIRMED |
| DEP-01 | MEDIUM | Dependencies | python-jose/passlib hết bảo trì, pin cũ | CONFIRMED |
| SEC-05 | LOW | Auth | Reset-password công khai + phân biệt 204/404 (chấp nhận, có tài liệu) | CONFIRMED by design |
| REL-03 | LOW | Telegram | `busy:N` không chặn N ngoài khoảng 5–480 | CONFIRMED |
| REL-04 | LOW | Frontend | `VITE_SHOP_PHONE` không sanitize trước `tel:`/hiển thị | LIKELY |
| REL-05 | LOW | Data growth | `conversations.messages` tăng vô hạn, không TTL | CONFIRMED |
| REL-06 | LOW | Logging | Log full URL (query string) ở INFO | CONFIRMED |
| INFO-01 | INFO | Kiến trúc | Lớp autogen `LLMManager` + deps liên quan là code chết | CONFIRMED |
| INFO-02 | INFO | Vận hành | `main.py` còn endpoint/socket-info + test client template; health cần trailing slash | CONFIRMED |

## Detailed Findings

### SEC-01 — Socket.IO bỏ qua `token_version` khi xác thực

- **Severity:** MEDIUM
- **Confidence:** CONFIRMED (đọc code trực tiếp)
- **Category:** Auth / thu hồi phiên

**Affected files:**
- `app/services/socketio_service.py:59-69` (`_authenticate`)
- Đối chiếu: `app/api/deps.py:60-61` (HTTP CÓ kiểm `tv`)
- `app/core/security.py:29-37` (`create_access_token_for` gắn `tv`)

**Evidence:**
`_authenticate` chỉ giải mã JWT, lấy `sub`, tra user và kiểm `is_active` — không
so `payload.get("tv")` với `user.token_version`. Trong khi đó mọi request HTTP đi
qua `get_current_user` đều so. Đổi mật khẩu (`UserRepository.set_password`,
`app/repositories/user.py:46-52`) tăng `token_version` và xoá refresh token, nhưng
kết quả chỉ hiệu lực với REST, không với kênh Socket.IO.

**Impact:**
Access token JWT sống `JWT_EXPIRE_MINUTES` (mặc định 30 phút — `.env.example:32`).
Sau khi nạn nhân đổi mật khẩu, token cũ vẫn chat/đặt/huỷ lịch qua socket cho tới
khi hết hạn. Ngoài ra deactive user (`is_active=False`) có hiệu lực vì `_authenticate`
có tra lại user — điểm này đúng.

**How to reproduce/verify (an toàn, local):**
1. Đăng nhập user thường, lấy access token.
2. Đổi mật khẩu qua `/api/v1/auth/reset-password`.
3. REST `/auth/me` trả 401; connect Socket.IO bằng token cũ → vẫn `connected`.

**Recommended fix (tối thiểu):**
Trong `_authenticate`, thêm đúng dòng HTTP đang dùng:
`if payload.get("tv", 0) != user.token_version: return None`.

**Suggested regression test:**
`tests/test_socketio_chat.py`: connect với token phát trước khi tăng
`token_version` → assert handshake bị từ chối; token mới → được chấp nhận.

---

### SEC-02 — `send_message` không có rate limit, không tuần tự hóa

- **Severity:** MEDIUM
- **Confidence:** CONFIRMED ở tầng code; mức độ thiệt hại chi phí cần runtime
- **Category:** Abuse / DoS / chi phí LLM

**Affected files:**
- `app/services/socketio_service.py:50-57, 75-90` (`send_message`, `handle_message`)
- `app/services/rate_limit.py` (đã có hạ tầng, chưa dùng cho chat)

**Evidence:**
Mỗi `send_message` chạy trọn một `run_turn` — ít nhất 2 lượt LLM (supervisor +
subagent), thêm timeparse khi nhắc thời gian. Không có kiểm hạn mức nào, không có
cờ "lượt đang chạy" cho sid/user. Rate limit mới gắn ở login, reset-password và
booking (`booking:user:` — `app/services/appointment.py:54-56`).

**Impact:**
Một khách (tài khoản hợp lệ) giữ socket và phát liên tục sẽ nhân bản chi phí LLM
theo ý muốn; vì hệ thống chạy ĐÚNG 1 worker (AGENTS.md), tải nặng còn làm chậm
luồng Telegram và khách khác. Không leo thang quyền, nhưng là lỗ hổng chi phí
thực tế với LLM trả tiền.

**How to reproduce/verify:**
Phát N `send_message` liên tiếp qua client socket (script node socket.io-client)
và đếm số turn tạo ra trong log — không có gì chặn.

**Recommended fix (tối thiểu):**
`rate_limit.check_and_hit(f"chat:user:{user_id}", limit, window)` ở đầu
`handle_message`; nếu vượt → emit `error` với câu tiếng Việt lịch sự. Cân nhắc
thêm cờ từ chối khi user đã có turn đang chạy (liên quan REL-01).

**Suggested regression test:**
Bắn quá hạn mức qua `SocketIOService.handle_message` với `run_turn` giả → assert
không gọi agent và có event lỗi thân thiện.

---

### SEC-03 — Compose dev: Mongo không auth + mongo-express admin/admin

- **Severity:** MEDIUM (đã ghi rõ dev-only; sẽ thành CRITICAL nếu mang lên prod)
- **Confidence:** CONFIRMED
- **Category:** Secrets / cấu hình triển khai

**Affected files:**
- `docker-compose.yml:20-52` (mongo expose 27017, mongo-express 8081 admin/admin)
- `CONTEXT.md` phần "Chưa có — cần trước khi mở cho khách thật" đã thừa nhận

**Evidence:**
Mongo expose `27017:27017` không username/password; mongo-express với
`ME_CONFIG_BASICAUTH_USERNAME=admin`, `...PASSWORD=admin` cho phép đọc/sửa toàn bộ
dữ liệu khách (tên, SĐT, lịch) từ trình duyệt.

**Impact:**
Chỉ chấp nhận được trên máy dev cục bộ. Mọi deployment ra mạng (kể cả LAN quán)
là lộ PII khách và mất toàn quyền sửa lịch.

**How to reproduce/verify:**
`docker compose ps` + mở `http://localhost:8081` — thấy dữ liệu mà không cần đăng
nhập Mongo.

**Recommended fix:**
Tách `docker-compose.prod.yml` (đã lên kế hoạch Plan 5): bỏ mongo-express, bật
auth Mongo, không expose 27017 ra ngoài mạng app. Giữ file dev nhưng thêm cảnh
báo ngay đầu file.

**Suggested regression test:**
Không test tự động hợp lý; checklist triển khai + CI lint compose (không có
`mongo-express` trong file prod).

---

### SEC-04 — CORS + credentials thiếu guard cho wildcard

- **Severity:** LOW
- **Confidence:** LIKELY — chỉ khai thác được khi cấu hình `ALLOWED_ORIGINS=*`
  (hiện tại `.env.example:47` là `http://localhost:3000`, không có wildcard)
- **Category:** Secrets / cấu hình

**Affected files:**
- `main.py:152-158` (`CORSMiddleware`, `allow_credentials=True`, methods/headers `*`)
- `app/core/config.py:84-91` (parse danh sách origin, không chặn `*`)

**Evidence:**
`allow_credentials=True` với danh sách origin cụ thể thì an toàn. Nhưng không có
gì chặn giá trị `*`: với Starlette, `*` + credentials dẫn tới echo origin của
request ở preflight — tương đương cho phép mọi site gửi request kèm cookie.
Refresh cookie `SameSite=strict` (`app/api/v1/routers/auth.py:34`) giảm đáng kể
nguy cơ vì cookie không gửi cross-site; tuy nhiên header `Authorization` do JS
nắm thì site khác vẫn mượn được nếu có XSS/origin bị tin cậy.

**Impact:**
Nếu ai đó đặt `ALLOWED_ORIGINS=*` (rất dễ khi "chạy cho nhanh"), mọi website đều
gọi được API với phiên nạn nhân.

**How to reproduce/verify:**
Đặt `ALLOWED_ORIGINS=*`, gửi preflight với `Origin: https://evil` → xem header
`Access-Control-Allow-Origin` trả về.

**Recommended fix (tối thiểu):**
Trong `parse_cors_origins`: nếu kết quả chứa `"*"` và `env == "prod"` thì ném lỗi
khởi động ngay (fail-fast, đúng triết lý Mongo fail-hard).

**Suggested regression test:**
`tests/test_config.py`: `ALLOWED_ORIGINS=*` + `env=prod` → `Settings()` raise.

---

### REL-01 — Hai lượt chat song song của cùng một user giẫm nhau

- **Severity:** MEDIUM
- **Confidence:** LIKELY (suy ra từ code; cần reproduce runtime)
- **Category:** Concurrency / correctness

**Affected files:**
- `app/services/socketio_service.py:75-90` (không khóa theo user)
- `app/agents/booking_graph/tools.py:118-120` (`set_pending`)
- `app/agents/booking_graph/confirm.py:49-67` (đọc rồi xoá pending)
- `frontend/src/hooks/useAgentStream.ts:47-50` (1 `streamingId` cho mọi turn)

**Evidence:**
Khách bấm Gửi hai lần nhanh (hoặc 2 tab cùng tài khoản): hai `run_turn` chạy song
song. `propose_appointment` của turn này có thể ghi đè `pending_confirmation` mà
turn kia vừa hỏi; `complete` của hai turn trộn vào cùng một bong bóng vì hook chỉ
có một `streamingId.current`; lịch sử chat nối vào không đảm bảo thứ tự
(`ConversationService.append` — `$push` riêng lẻ).

**Impact:**
Sai lệch hội thoại, có thể xác nhận nhầm giờ nếu pending bị đổi giữa hai lượt.
Xác suất thấp với khách lớn tuổi nhưng là race có thật.

**Recommended fix (tối thiểu):**
Từ chối lượt mới khi user đang có lượt chạy (một `set[user_id]` trong
`SocketIOService`, cleanup khi turn xong) — trả câu "con đang tra, cô chú đợi chút
ạ". Giải pháp này đồng thời chặn một phần SEC-02.

**Suggested regression test:**
Gọi song song hai `handle_message` cho một user với `run_turn` giả có delay → assert
lượt thứ hai nhận event từ chối, không có interleaving.

---

### REL-02 — Giờ mở cửa không validate định dạng

- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Category:** Input validation

**Affected files:**
- `app/api/v1/schemas.py:72-75` (`ShopHoursRequest.open_time/close_time: str`)
- `app/services/shop.py:96-98` (`_to_minutes`: `hhmm.split(":")` + `int`, không bẫy)

**Evidence:**
Admin PUT `/shop/hours` với `"25:99"` hoặc `"abc"` vẫn 200 (Pydantic chỉ kiểm kiểu
str). Sau đó mọi `fits_before_closing`/`is_within` gọi `_to_minutes` ném
`ValueError` không phải `AppError` → khách đặt lịch nhận 500 lạ, agent chat rơi
vào nhánh lỗi chung.

**Impact:**
Một cú gõ nhầm của admin làm hỏng toàn bộ luồng đặt lịch cho tới khi sửa lại.

**Recommended fix (tối thiểu):**
Pydantic validator cho `open_time/close_time`: regex `^([01]\d|2[0-3]):[0-5]\d$`,
và `close > open`. Trả 422 với thông báo tiếng Việt.

**Suggested regression test:**
PUT giờ rác → 422; giờ hợp lệ → 200; sau đó `find_free_slots` hoạt động.

---

### DEP-01 — Dependency hết bảo trì / pin cũ

- **Severity:** MEDIUM
- **Confidence:** CONFIRMED (từ `requirements.txt`)
- **Category:** Supply chain

**Affected files:**
- `requirements.txt:2-11` (`fastapi==0.104.1`, `python-jose[cryptography]==3.3.0`,
  `passlib[bcrypt]==1.7.4`, `motor==3.3.2`, `pymongo==4.6.0`, `httpx==0.25.2`,
  `uvicorn==0.24.0`)

**Evidence:**
`python-jose` không còn maintainer và có CVE công bố (JWE/JWT parsing);
`passlib` ngừng phát hành nhiều năm (đó là lý do phải pin `bcrypt==4.0.1` —
`requirements.txt:7`). Repo đã có sẵn `pip-audit`/`bandit` trong dev deps nhưng
không có CI chạy chúng (AGENTS.md: "No CI").

**Impact:**
Rủi ro chuỗi cung ứng tăng dần; bản vá FastAPI/Starlette không tới. Chưa có bằng
chứng khai thác trực tiếp vào cách dùng hiện tại (JWT HS256, bcrypt) nên không
nâng severity.

**Recommended fix:**
Lộ trình: thay `python-jose` bằng `PyJWT`; chạy `pip-audit` định kỳ; nâng
FastAPI/uvicorn/httpx theo minor. KHÔNG làm vội trong audit này.

**Suggested regression test:**
CI job `pip-audit` + test auth hiện có làm regression khi đổi thư viện JWT.

---

### SEC-05 — Reset-password công khai, phân biệt 204/404

- **Severity:** LOW (rủi ro đã chấp nhận, có tài liệu)
- **Confidence:** CONFIRMED by design
- **Category:** Auth

**Affected files:**
- `app/api/v1/routers/auth.py:90-95` (endpoint trả `204`)
- `app/services/auth.py:89-109` (ném `NotFoundError` → 404 cho SĐT không tồn tại)

**Evidence:**
Ai biết SĐT thì đổi được mật khẩu; endpoint còn cho dò SĐT nào có tài khoản
(204 vs 404). Cả hai được ghi nhận và chấp nhận trong `CONTEXT.md` (mục bảo mật)
với lý do tiệm nhỏ, khách quen. Đã có rate limit 5 lần/giờ theo cả SĐT lẫn IP
(`auth.py:22-23, 97-98`).

**Impact:**
Giới hạn: kẻ biết SĐT khách chiếm được phiên khách đó (không phải admin).

**Recommended fix:**
Không đổi hành vi (quyết định sản phẩm). Cân nhắc luôn trả 204 để đóng đường dò
SĐT — chi phí thấp, không phá luồng.

**Suggested regression test:**
Đã có trong `tests/test_auth_service.py` / `test_rate_limit.py`; thêm test
"reset SĐT lạ luôn 204" nếu chọn phương án vá.

---

### REL-03 — Telegram `busy:N` không chặn N ngoài khoảng

- **Severity:** LOW
- **Confidence:** CONFIRMED
- **Category:** Input validation

**Affected files:**
- `app/telegram/handlers.py:92-93` (`int(data.split(":", 1)[1])` → `set_busy`)
- Đối chiếu REST có chặn: `app/api/v1/schemas.py:69` (`ge=5, le=480`)

**Evidence:**
Callback data do Telegram chuyển có thể chứa `busy:99999` nếu admin (hoặc người
nắm chat_id admin) sửa inline keyboard. `set_busy(99999)` đặt bận ~69 ngày; không
có gì chặn. REST thì validate 5–480 phút.

**Impact:**
Thẻ trạng thái "bận" treo rất lâu; chỉ admin whitelist mới làm được, phải tự sửa
lại bằng "Tôi rảnh rồi".

**Recommended fix:**
Clamp hoặc từ chối ngoài 5–480 trong handler, dùng chung constant với schema.

**Suggested regression test:**
`tests/test_telegram_handlers.py`: callback `busy:99999` → không đổi trạng thái.

---

### REL-04 — `VITE_SHOP_PHONE` không sanitize

- **Severity:** LOW
- **Confidence:** LIKELY (build-time env, khó bị tấn công thực tế)
- **Category:** Frontend input

**Affected files:**
- `frontend/src/screens/ChatScreen.tsx:22, 133-137` (`href={`tel:${shopPhone}`}`)

**Evidence:**
`shopPhone` đọc từ `import.meta.env`, render thẳng vào `href` và text. Nếu biến
môi trường build chứa `javascript:` hoặc ký tự đặc biệt, link "Gọi cho tiệm"
thành URL lạ. Vì là biến build do chính mình đặt nên thực tế khó khai thác.

**Recommended fix:**
Validate `^[\d +.-]+$` trước khi render; không render link nếu không khớp (code đã
có nhánh không render khi rỗng — mở rộng điều kiện).

**Suggested regression test:**
Test component với env rác → không render `tel:` link.

---

### REL-05 — Lịch sử hội thoại tăng vô hạn

- **Severity:** LOW
- **Confidence:** CONFIRMED
- **Category:** Data growth / riêng tư

**Affected files:**
- `app/services/conversation.py:37-46` (`append` — `$push`, không cap)
- `app/services/conversation.py:48-52` (`_all_messages` đọc TOÀN BỘ mỗi lần)

**Evidence:**
Cắt theo ngày/token chỉ áp lúc NẠP vào prompt (`history`), không áp lúc LƯU.
Document `conversations` của khách nhắn nhiều phình mãi; mọi thao tác
(`history`, `list_days`, `messages_on`) đều đọc toàn bộ mảng.

**Impact:**
Chi phí đọc tăng dần; về lý thuyết chạm trần 16MB/document sau thời gian rất dài.
Lưu trữ dữ liệu khách vô thời hạn cũng là điểm nên xem xét về riêng tư.

**Recommended fix:**
Cắt mảng khi append (giữ N ngày gần nhất) hoặc tách collection theo ngày.

**Suggested regression test:**
Append vượt ngưỡng → document được tỉa, `messages_on` ngày cũ vẫn đúng.

---

### REL-06 — Log full URL ở INFO

- **Severity:** LOW
- **Confidence:** CONFIRMED
- **Category:** Logging / PII

**Affected files:**
- `main.py:191-210` (`log_requests` — `url=str(request.url)`)

**Evidence:**
Mọi request log nguyên URL kể cả query string. Hiện tại endpoint nhạy cảm dùng
body/cookie nên chưa lộ gì, nhưng lỡ có ai thêm token vào query (pattern phổ biến
ở socket.io polling fallback) thì vào log.

**Recommended fix:**
Chỉ log `request.url.path`; query string để DEBUG.

**Suggested regression test:**
Không cần; thay đổi một dòng.

---

### INFO-01 — Lớp AutoGen `LLMManager` là code chết

- **Severity:** INFO
- **Category:** Maintainability

**Affected files:**
- `app/api/deps.py:22-40` (`get_llm_client`, `get_autogen_llm_client` — không router nào dùng)
- `app/infrastructure/llm.py` (khởi động autogen client cho azure/openai/anthropic/gemini)
- `main.py:54-58` (gọi `initialize_llm_clients` lúc startup)

**Evidence:**
Luồng chat thật đi `build_chat_model` (langchain). Không endpoint nào inject
`get_llm_client`; grep toàn `app/` chỉ thấy định nghĩa. Startup vẫn dựng client
autogen không dùng tới.

**Recommended fix:**
Khi rảnh: bỏ manager + deps, giữ phần validate config nếu cần. Không khẩn.

---

### INFO-02 — Tàn dư template trong `main.py`

- **Severity:** INFO
- **Category:** Maintainability

**Affected files:**
- `main.py:217-262` (`/static/socketio_test.html`, `/socket-info` với ví dụ JWT)
- `app/api/v1/routers/health.py` — health cần trailing slash (`/api/v1/health/`)

**Evidence:**
Trang test client và endpoint mô tả sự kiện là tàn dư template gốc; vô hại (không
secret) nhưng là bề mặt thừa trên prod. Ghi chú trailing-slash đã có trong
AGENTS.md. Thêm: `.env.example:47` để `ALLOWED_ORIGINS=http://localhost:3000`
nhưng Vite dev mặc định chạy ở `:5173` — file ví dụ hơi lệch so với frontend
thực tế (file `.env` thật bị ignore nên không ảnh hưởng runtime).

**Recommended fix:**
Gắn các endpoint này sau `settings.env != "prod"` như `/docs`.

## Positive Findings

Những chỗ làm tốt, cần giữ:

1. **Authorization nhất quán 2 tầng** — admin route chặn backend bằng
   `require_admin` (`app/api/deps.py:66-70`), đặt lịch hộ kiểm quyền trong router
   (`appointments.py:36-43`), huỷ lịch kiểm ownership trong service
   (`appointment.py:83-84`). Frontend `RequireAuth` tự nhận mình không phải bảo
   mật (`RequireAuth.tsx:4-8`).
2. **Chống trùng giờ nguyên tử** — unique partial index trên `slot_keys` lọc
   `status="booked"` (`database.py:71-76`), repo dịch `DuplicateKeyError` →
   `SlotTakenError` trước khi handler PyMongoError kịp biến nó thành 503
   (`appointment.py:42-43`). Có test 409 (`test_api_flow.py`).
3. **Refresh token chuẩn BCP** — hash SHA-256 khi lưu, rotation có cửa sổ ân hạn
   30s, phát hiện reuse xoá cả family (`auth.py:126-153`), cookie
   HttpOnly+SameSite=strict+path-scoped (`auth.py:25-36`). Test đầy đủ trong
   `test_auth_cookies.py`, kể cả replay token bị đánh cắp.
4. **Token version** thu hồi access token khi đổi mật khẩu trên tầng HTTP
   (`deps.py:60-61`) — đúng, chỉ socket là thiếu (SEC-01).
5. **Không có tool ghi lịch** — `test_tools.py` có test
   `test_there_is_NO_tool_that_writes_an_appointment`; lịch ghi từ giá trị DB qua
   node confirm, không từ chuỗi model (`confirm.py:59-62`), khẳng định bằng
   `test_propose_then_yes_books_the_time_from_MONGO`.
6. **Cách ly dữ liệu giữa khách** — tool đóng `user` trong closure
   (`tools.py:52-57`), `user_id` không bao giờ là tham số AI điều khiển;
   `test_socketio_chat.py` có test event chỉ tới đúng socket của user đó.
7. **NotificationService** một cửa tỏa tin, nuốt lỗi từng kênh
   (`notifications.py:35-44`) — Telegram chết không hỏng đặt lịch; có
   `test_notification_wiring.py`.
8. **Telegram fail-soft + whitelist** — IM lạ bị bỏ qua im lặng
   (`handlers.py:52-55`), offset vẫn tiến khi handler lỗi (`bot.py:56-63`),
   `getUpdates` không nuốt lỗi để backoff còn tác dụng (`client.py:106-120`).
9. **Timezone nhất quán** — mọi "bây giờ" qua `now_utc()`, naive datetime từ
   client được gắn TZ VN (`clock.py:14-22`), có test offset UTC
   (`test_listed_appointments_keep_their_utc_offset`).
10. **Frontend an toàn** — access token chỉ trong memory (test enforce không
    localStorage trong `AuthContext.test.tsx:201-205`), không
    `dangerouslySetInnerHTML`/`innerHTML` nào trong `src/` (đã grep), offline
    queue + canonical complete + regression StrictMode (`useAgentStream.test.ts`
    test W/X).
11. **Provider portable mới** — `openai_compatible` chỉ qua ENV, lỗi nêu tên biến
    thiếu, không hardcode; Azure giữ nguyên (`app/agents/llm.py`, 9 test trong
    `tests/test_provider_config.py`).

## Test Coverage Gaps

Đã phủ tốt: auth cookie/refresh, double booking 409, quyền admin/khách, tool
authorization, confirm flow, socket event routing, Telegram handlers, provider
config, StrictMode streaming.

Cần thêm (theo finding):

| Finding | Test thiếu |
|---------|-----------|
| SEC-01 | Socket từ chối token có `tv` cũ |
| SEC-02 | Chat vượt hạn mức → không gọi agent |
| SEC-04 | `ALLOWED_ORIGINS=*` + prod → lỗi khởi động |
| REL-01 | Hai `handle_message` song song → lượt hai bị từ chối |
| REL-02 | PUT giờ rác → 422 |
| REL-03 | `busy:99999` → không đổi trạng thái |
| REL-05 | Tỉa lịch sử khi append vượt ngưỡng |

## Architecture / Maintainability Notes

- Phân tầng sạch: router mỏng → service → repository; agent/Telegram/REST cùng
  đi `AppointmentService` — đúng nguyên tắc AGENTS.md, không thấy logic đặt lịch
  nằm ngoài `app/services/`.
- Code chết nên dọn khi tiện: `LLMManager` autogen + 2 deps (INFO-01),
  tàn dư template (INFO-02).
- `pyproject.toml` lệch `requirements.txt` (pyproject thiếu langchain/langgraph/
  langfuse) — AGENTS.md đã chốt requirements.txt là nguồn thật; nên đồng bộ hoặc
  bỏ block dependencies của pyproject để khỏi gây hiểu nhầm.
- Lỗi nghiệp vụ dùng tiếng Việt thống nhất (`app/core/errors.py`) — tốt cho UX,
  nhưng message hardcoded trong exception class khiến i18n sau này khó; chấp nhận
  vì sản phẩm một ngôn ngữ.

## Prioritized Remediation Plan

### P0 — Fix immediately

Không có lỗi P0. Repo an toàn để tiếp tục phát triển.

### P1 — Before merge/release (mở cho khách thật)

1. SEC-01: thêm kiểm tra `token_version` vào `_authenticate` của Socket.IO + test.
2. SEC-02: rate limit `send_message` bằng `RateLimitService` sẵn có.
3. REL-02: validate giờ mở cửa (một cú gõ nhầm của admin hỏng cả luồng đặt lịch).
4. SEC-03: trước mọi deploy ra ngoài máy dev, tách compose prod (Mongo auth, bỏ
   mongo-express).

### P2 — Next iteration

5. REL-01: tuần tự hóa lượt chat theo user (giải luôn phần còn lại của SEC-02).
6. DEP-01: thay python-jose → PyJWT, nâng FastAPI/uvicorn/httpx, CI pip-audit.
7. SEC-04: guard wildcard CORS ở prod.
8. REL-03: clamp phút bận của Telegram về 5–480.
9. REL-05: chiến lược giữ lịch sử (cap ngày hoặc tách collection).

### P3 — Optional improvements

10. INFO-01/02: dẹp code chết autogen và endpoint template.
11. SEC-05: cân nhắc reset-password luôn trả 204.
12. REL-04/REL-06: sanitize `VITE_SHOP_PHONE`, log path thay vì full URL.

## Final Assessment

Codebase nhỏ nhưng kỷ luật cao: các quyết định bảo mật khó (refresh HttpOnly,
token version, partial unique index, tool không quyền ghi) đều được thực thi đúng
và có test bảo vệ — kể cả bug StrictMode vừa sửa cũng đã có regression test. Ba
việc MEDIUM cần làm trước khi mở cho khách thật (SEC-01, SEC-02, REL-02) đều là
sửa nhỏ, khoanh vùng rõ, mỗi việc một test. Chưa phát hiện lỗ hổng cho phép vượt
quyền, đọc dữ liệu khách khác, hoặc thực thi mã.

**Kết luận: an toàn để tiếp tục phát triển/review; làm P1 trước khi phát hành.**
