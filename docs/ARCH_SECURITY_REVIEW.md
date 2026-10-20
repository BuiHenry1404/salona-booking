# Review kiến trúc và bảo mật — 2026-10-04

Review chỉ đọc code trên nhánh `fix/clock-and-dead-data` (commit `5e40064`).
Không sửa code, không chạy test, không dựng app để khai thác thật.
Review riêng phần agent LangGraph: [`AGENT_GRAPH_REVIEW.md`](AGENT_GRAPH_REVIEW.md).

## Cách làm

| Nguồn | Nội dung |
|---|---|
| Swarm agentbox (29 agent) | Mỗi agent trong `~/.agentbox/agents/` được chạy bằng một subagent Claude, theo đúng `agent.yaml` + `persona.md` + skill của nó, trả `findings.v1`. Gộp theo `id`, gate như `swarm.yaml` (chặn khi có critical/high). Gồm 6 agent `architecture-*` (swarm `code-review-architecture`), 21 agent `app-sec-*` (swarm `security-app-sec`), `ai-llm-hunt-llm-ai`, `crypto-auth-jwt-misconfigurations`. **Không dùng runtime/CLI agentbox** (MCP agentbox không kết nối được), code không gửi ra ngoài. |
| Kiểm chứng đối kháng | Mỗi phát hiện critical/high của swarm có một agent riêng đọc lại code và cố bác bỏ. |
| 2 reviewer tổng quát | Một kiến trúc, một bảo mật, đọc cả `CONTEXT.md`/`REPO_AUDIT.md`/`PROD_CHECKLIST.md` để không báo lại cái đã biết. Dùng để đối chiếu chéo. |
| Kiểm tay | Các mục **[đã kiểm]** dưới đây đã được đối chiếu trực tiếp với code khi viết file này. |

**Verdict của swarm: CHẶN** (`has_high`). 38 phát hiện: 2 high (cùng một lỗi), 9 medium, 26 low, 1 info.
Không agent nào lỗi. Tổng hợp với 2 reviewer còn lại ra danh sách dưới đây (đã gộp trùng).

Các ID cũ (`SEC-xx`, `REL-xx`, `DEP-01`, `INFO-xx`) xem `REPO_AUDIT.md`. ID mới trong file này: `S-xx` (bảo mật), `A-xx` (kiến trúc / độ tin cậy).

---

## Phải xong trước khi lên prod

| # | Việc | Vì sao |
|---|---|---|
| 1 | **S-01** chặn admin ở `reset-password` | Người lạ chiếm được tài khoản chủ tiệm bằng 1 request |
| 2 | **A-01** dời lịch chồng giờ làm mất lịch cũ | Mất lịch khách, chủ tiệm không được báo |
| 3 | **S-03** validate cấu hình prod (`JWT_SECRET`, cookie, CORS) | Quên điền `JWT_SECRET` = ai cũng ký được token admin |
| 4 | **S-02** IP thật sau reverse proxy | Không sửa thì 10 lần sai mật khẩu khoá đăng nhập của cả tiệm |
| 5 | **A-02** đường cấp `.env` cho container prod | Làm đúng checklist hiện tại thì container không khởi động |
| 6 | **S-04** (gồm SEC-01) kiểm lại user trên socket | Khách bị khoá / đổi mật khẩu vẫn chat, đặt, hủy lịch được |
| 7 | **S-05** `.dockerignore` | Image đang chứa credential `.agentbox/.project-auth` |
| 8 | **S-06** script tạo admin cho prod | Đường duy nhất hiện có tạo admin với mật khẩu công khai |
| 9 | **S-07** giới hạn độ dài tin chat | Một tài khoản đốt được hàng triệu token Azure mỗi giờ |

`PROD_CHECKLIST.md` cần cập nhật theo bảng này — đặc biệt mục reset admin đang nằm ở "Bước 3 — Khi rảnh".

---

## Bảo mật

### S-01 — Nghiêm trọng — `reset-password` công khai chiếm được admin [đã kiểm]

- **Vị trí:** `app/services/auth.py:89-109`, `app/api/v1/routers/auth.py:90-95`
- **Vấn đề:** endpoint không cần đăng nhập; `reset_password` chỉ kiểm SĐT có trong DB, **không nhìn `role`**. Docstring nói "rủi ro đã chấp nhận", nhưng `CONTEXT.md:208-210` chỉ chấp nhận cho khách ("Admin thì không"). Spec `docs/superpowers/specs/2026-08-15-admin-password-reset-telegram-design.md` đã ghi lỗ này và cách vá, hẹn "sau Plan 3" — Plan 3 đã xong mà code chưa vá. `REPO_AUDIT` SEC-05 ghi "không phải admin" là sai so với code.
- **Khai thác:** SĐT chủ tiệm thường in trên biển hiệu → `POST /api/v1/auth/reset-password` với SĐT đó và mật khẩu mới → đăng nhập admin: xem tên + SĐT mọi khách, toàn bộ lịch, hủy lịch bất kỳ, tạo thêm admin để giữ quyền. Hạn mức 5 lần/giờ không cản được vì chỉ cần 1 lần.
- **Sửa:** `if not user or user.role == "admin": raise NotFoundError(...)` cùng một thông báo (không lộ SĐT nào là admin). Không phụ thuộc lệnh khôi phục qua Telegram — tạm thời admin khôi phục bằng script (xem S-06). Test theo spec.
- **Nguồn:** `app-sec-auth-bypass` + `app-sec-idor-bola-check` (high, kiểm chứng: không bác bỏ được), reviewer bảo mật (nghiêm trọng).

### S-02 — Cao — hạn mức đăng nhập/reset theo IP thành ổ khoá chung sau proxy

- **Vị trí:** `app/api/v1/routers/auth.py:53, 94` (`request.client.host`), `Dockerfile` CMD không có `--forwarded-allow-ips`
- **Vấn đề:** uvicorn chỉ tin `X-Forwarded-For` từ `127.0.0.1`. Nginx/Caddy ở container khác → mọi request mang IP của proxy → `login:ip:*` và `pwreset:ip:*` dùng chung cho cả tiệm. `check()` chặn trước khi so mật khẩu nên chặn cả người gõ đúng.
- **Khai thác:** 10 lần đăng nhập sai / 15 phút → không ai đăng nhập được, lặp lại mỗi 15 phút. 5 lần reset / giờ → khoá quên-mật-khẩu của mọi khách. Sửa ẩu bằng `--forwarded-allow-ips="*"` khi compose còn publish `8000:8000` thì ngược lại: đổi header là thoát hạn mức.
- **Sửa:** `--proxy-headers --forwarded-allow-ips=<IP proxy>`; app chỉ nghe trong network nội bộ (không publish 8000). Throttle theo IP nên đặt ở proxy (đã có trong `PROD_CHECKLIST` bước 2).
- **Nguồn:** reviewer bảo mật.

### S-03 — Trung bình — cấu hình prod không fail-fast; `JWT_SECRET` rỗng vẫn chạy [đã kiểm]

- **Vị trí:** `app/core/config.py:20` (`jwt_secret: SecretStr` không ràng buộc), `.env.example:13` (`JWT_SECRET=` rỗng)
- **Vấn đề:** reviewer đã thử `jose.jwt.encode(..., "", "HS256")` — ký và decode được. Copy `.env.example` quên điền là app chạy bình thường. `jwt_algorithm` cũng không bị giới hạn.
- **Khai thác:** tự ký `{"sub": <id admin>, "tv": 0}` bằng khoá rỗng/yếu.
- **Sửa:** validator trong `Settings`: `jwt_secret` ≥ 32 byte; `jwt_algorithm` ∈ {HS256}; khi `env == "prod"` bắt buộc `cookie_secure=True`, `debug=False`, không có `*` trong `allowed_origins` (đóng luôn **SEC-04**).
- **Nguồn:** reviewer bảo mật, `crypto-auth-jwt-misconfigurations` (low).

### S-04 — Trung bình — socket đã xác thực thì sống mãi (mở rộng SEC-01)

- **Vị trí:** `app/services/socketio_service.py:37-72`
- **Vấn đề:** ngoài **SEC-01** (không kiểm `tv`):
  - `send_message` chỉ kiểm `if user:`, không kiểm `is_active`. `REPO_AUDIT` SEC-01 nói "deactive có hiệu lực" — chỉ đúng lúc handshake.
  - `exp` của JWT chỉ kiểm lúc connect.
  - Quyền vào room `admins` quyết định một lần; admin bị hạ quyền vẫn nhận `appointment_created` có SĐT khách.
- **Khai thác:** admin khoá khách quấy rối → tab đang mở của khách vẫn chat, đặt, hủy lịch.
- **Sửa:** mỗi `send_message` kiểm lại `is_active` + `tv` + `exp` (lưu theo sid); đổi mật khẩu / khoá user thì `sio.disconnect` các sid của user đó.
- **Nguồn:** `app-sec-auth-bypass`, `crypto-auth-jwt-misconfigurations` (medium), cả 2 reviewer.

### S-05 — Trung bình — Docker image chứa credential AgentBox và transcript [đã kiểm]

- **Vị trí:** `.dockerignore` không loại `.agentbox/`, `.superpowers/`, `*.txt`, `frontend/`, `scripts/`, `static/`; `Dockerfile` dùng `COPY . .`
- **Vấn đề:** `.agentbox/.project-auth` có trong thư mục repo (đã gitignore nhưng **không** dockerignore) → vào image. Các file `hoi-thoai-*.txt`, `after-*.txt` cũng vào.
- **Sửa:** chuyển sang allowlist: `COPY app/ main.py requirements.txt ./`. Ít nhất thêm các mục trên vào `.dockerignore`.
- **Nguồn:** reviewer bảo mật.

### S-06 — Trung bình — chỉ có script seed để tạo admin, mật khẩu công khai [đã kiểm]

- **Vị trí:** `scripts/seed_dev_users.py:19` (`0901234567` / `chutiem123`, cũng ghi ở `RUNBOOK.md`); script không kiểm `settings.env`
- **Vấn đề:** prod không có đường nào khác tạo admin đầu tiên → người vận hành dễ chạy luôn script seed.
- **Sửa:** script seed `sys.exit` khi `ENV=prod`; thêm `scripts/create_admin.py` (SĐT qua tham số, mật khẩu qua `getpass`) — dùng luôn làm đường khôi phục admin trong lúc chờ Telegram; thêm bước vào checklist.
- **Nguồn:** `app-sec-hardcoded-secrets-scanner` (medium), reviewer bảo mật.

### S-07 — Trung bình — tin chat không giới hạn độ dài/kiểu (OWASP LLM10) [đã kiểm]

- **Vị trí:** `app/services/socketio_service.py:88-91` (không có kiểm `len`/`isinstance`), `app/services/conversation.py:120`
- **Vấn đề:** engine.io mặc định nhận gói ~1 MB. Hạn mức 30 tin/giờ đếm số tin, không đếm token; một lượt gọi LLM nhiều lần (supervisor, respond, timeparse, rewrite). Tin dài lưu vào một document `conversations` duy nhất → có thể chạm trần 16 MB, sau đó mọi append lỗi và chat của user đó hỏng hẳn. `message` là dict/số thì `.strip()` ném lỗi ngoài `try`.
- **Sửa:** `if not isinstance(message, str) or len(message) > 1000: emit error`; `max_http_buffer_size` ~16 KB trong `AsyncServer`; `maxLength` ở ô nhập frontend.
- **Nguồn:** `ai-llm-hunt-llm-ai` (medium), reviewer bảo mật.

### S-08 — Trung bình — thiếu log sự kiện bảo mật

- **Vị trí:** `app/services/auth.py:71` (đăng nhập), `app/api/v1/routers/auth.py:104` (tạo user/admin, đổi giờ mở cửa, bận/rảnh), `app/api/deps.py:47` (401/403), `app/services/rate_limit.py:71` (bị khoá), `socketio_service.py:66` (`except` nuốt lỗi xác thực socket), `main.py:178` (không có request id)
- **Vấn đề:** dò mật khẩu, bị khoá, admin cấp quyền admin cho người khác — không để lại dấu vết nào để phát hiện hay điều tra. Chỉ có `password_reset` được log.
- **Sửa:** log `warning` có cấu trúc (phone đã chuẩn hoá, ip, kết quả) cho login fail/success, lockout, 403, tạo user (kèm role), thay đổi cấu hình tiệm; thêm request id vào middleware log.
- **Nguồn:** `app-sec-logging-monitoring-failures` (2 medium, 4 low).

### S-09 — Thấp/Trung bình — hạn mức đăng nhập là check-then-act [đã kiểm]

- **Vị trí:** `app/services/auth.py:62-73`
- **Vấn đề:** `check()` trước, `hit()` sau khi so mật khẩu sai. Gửi đồng thời N request thì cả N qua được `check` → dò được nhiều hơn `LOGIN_MAX_ATTEMPTS` mỗi cửa sổ. bcrypt làm chậm nhưng không chặn. (Reset password dùng `check_and_hit` nên không dính.)
- **Sửa:** ghi dấu trước (`check_and_hit`) rồi xoá dấu khi đăng nhập đúng; hoặc dùng `find_one_and_update` đếm nguyên tử.
- **Nguồn:** `app-sec-race-condition-toctou` (medium).

### S-10 — Thấp/Trung bình — một tài khoản lấp kín lịch tiệm

- **Vị trí:** `app/services/appointment.py:50, 74`
- **Vấn đề:** chỉ có hạn mức đặt theo giờ (`booking_max_per_hour`), không có trần số lịch đang hoạt động, và service không chặn ngày quá xa (horizon 90 ngày chỉ nằm ở tầng agent/state — xem A-12). Một tài khoản (hoặc tài khoản khách bị chiếm qua SEC-05) đặt dần kín lịch.
- **Sửa:** trần số lịch `booked` tương lai mỗi khách (vd. 3) và horizon trong `AppointmentService`.
- **Nguồn:** `app-sec-business-logic` (medium).

### Các mục thấp

| ID | Vấn đề | Vị trí | Sửa |
|---|---|---|---|
| S-11 | Stack Langfuse publish `0.0.0.0:3100`, secret/mật khẩu mặc định trong repo, trace chứa tên + SĐT khách | `docker-compose.langfuse.yml:31-63` | Bind `127.0.0.1`, secret mới cho prod, mask SĐT |
| S-12 | Socket.IO rơi về polling → `sid` nằm trong URL bị log mức INFO; ai đọc log có thể dùng phiên đó (**chưa kiểm chứng chạy thật**) | `main.py:175-194` | Làm **REL-06** (chỉ log path) |
| S-13 | Bot Telegram nhận id nhóm làm admin, xác thực theo `chat.id` không theo `from.id` → mọi thành viên nhóm xem được SĐT khách | `app/telegram/client.py:41-47`, `handlers.py:121-153` | Kiểm `from.id`, hoặc từ chối id âm |
| S-14 | Mật khẩu tối thiểu 4 ký tự, không có max (bcrypt cắt sau 72 byte) | `app/api/v1/schemas.py:27, 34` | `min_length` 6–8, `max_length=72` |
| S-15 | Xoay refresh token đua với reset mật khẩu có thể sinh refresh token sống sót qua thu hồi | `app/services/auth.py:132` | Kiểm `token_version` khi xoay, hoặc xoá theo `created_at < reset_at` |
| S-16 | Trang test `static/socketio_test.html` cùng origin API, nạp script CDN không SRI → script đó gọi được `/auth/refresh` | `static/socketio_test.html:7` | Làm **INFO-02**: API origin không phục vụ HTML/JS |
| S-17 | Code chết `verify_api_key_dependency`, so chuỗi bằng `==` | `app/api/deps.py:63-73`, `app/core/security.py:67-69` | Xoá cùng biến `API_KEY` |
| S-18 | Dependency có CVE: `python-jose 3.3.0` (**DEP-01**), `pymongo 4.6.0` (CVE-2024-5629), `python-multipart 0.0.6`/`starlette 0.27` (hiện không khai thác được vì không có endpoint form) — **chưa chạy `pip-audit`** | `requirements.txt` | Thay `python-jose` → `PyJWT`, nâng các gói còn lại |

### Đã kiểm, không thấy lỗ

- **IDOR qua REST:** `/mine`, `/conversations/*` lọc theo user đăng nhập; `DELETE /appointments/{id}` kiểm chủ sở hữu; đặt hộ chỉ admin; mọi route admin qua `require_admin`.
- **IDOR qua agent:** `user` đóng trong closure của tool, không phải tham số model điều khiển; `own_booked` kiểm chủ sở hữu.
- **Confirm lấy từ DB:** node `confirm` đọc `pending_confirmation` từ Mongo, không có tool ghi. Lỗ còn lại là bug `is_affirmative` đã ghi trong `CONTEXT.md`.
- **NoSQL injection:** không chỗ nào đưa dict người dùng vào filter (`app-sec-sql-nosql-injection`: 0 phát hiện).
- **XSS:** frontend không dùng `dangerouslySetInnerHTML`, tin bot render text thuần; Telegram gửi không `parse_mode`.
- **Secrets trong git history:** chỉ thấy placeholder. `.env` không bị track.
- 14 agent khác (command injection, RCE, SSTI, XXE, LDAP, deserialization, path traversal, upload, open redirect, CSRF/SSRF, host header, cache poisoning, request smuggling) trả về 0 phát hiện.

---

## Kiến trúc và độ tin cậy

### A-01 — Cao — dời lịch chồng giờ làm khách mất lịch cũ [đã kiểm]

- **Vị trí:** `app/services/appointment.py:121-128` (docstring `:108-112` mô tả sai)
- **Vấn đề:** nhánh `overlaps_itself` hủy lịch cũ **rồi mới** `create`. Các mốc mới nằm ngoài lịch cũ không được kiểm trước. `create` ném `SlotTakenError` → lịch cũ đã hủy, không khôi phục, không báo chủ tiệm. Docstring gọi đây là "race thuần", nhưng cửa sổ thực tế là 10 phút pending.
- **Kịch bản:** lịch A 9:00–10:00, xin dời 9:30 → `propose` thấy trống, giữ pending → khách B đặt 10:00 → A nói "ừ" → hủy 9:00, `create` 9:30 đụng mốc 10:00 → A nghe "giờ đó có người đặt", tưởng lịch 9:00 vẫn còn.
- **Sửa:** bọc `create` bằng try/except, lỗi thì lật lịch cũ về `booked` (`update_one({_id, status: "cancelled"}, ...)`, partial index vẫn bảo vệ). Thêm test đúng kịch bản trên — test hiện có chỉ phủ nhánh không chồng giờ.
- **Nguồn:** reviewer kiến trúc.

### A-02 — Trung bình — làm đúng `PROD_CHECKLIST` thì container prod không khởi động [đã kiểm]

- **Vị trí:** `app/core/config.py:6, 97-102`, `.dockerignore` (loại `.env*`), `docker-compose.yml:13-18`
- **Vấn đề:** `Settings` đọc `.env` (tương đối, `/app/.env`) rồi `/run/config/env.docker`. Ở dev `.env` có nhờ bind mount `.:/app`. Checklist 1.1 bảo bỏ mount, 1.4 bảo `.env` không nằm trong image, nhưng không nói cấu hình vào bằng đường nào → `ValidationError` lúc import, container restart liên tục.
- **Sửa:** compose prod mount `.env` prod vào `/app/.env:ro`; ghi vào checklist 1.1/1.4; sửa comment "chỉ đè vài khoá".

### A-03 — Trung bình — báo Telegram chạy nối tiếp trong đường đặt lịch, timeout 30s

- **Vị trí:** `app/services/appointment.py:71, 133, 148`, `app/services/notifications.py:35-44`, `app/telegram/client.py:67-93`
- **Vấn đề:** `create`/`cancel`/`reschedule` `await` toàn bộ fan-out; gửi lần lượt từng admin, mỗi lần timeout 30s. Telegram không tới được → mỗi lượt chốt lịch treo ~30s × số admin (dời lịch ×2). Khách gửi lại tin → rơi vào **REL-01**. Khả năng Telegram bị chặn ở VN **chưa kiểm**, cần thử trên máy chủ thật.
- **Sửa:** fan-out bằng `asyncio.create_task` (giữ tham chiếu mạnh) hoặc `wait_for(..., 3)`; hạ timeout `sendMessage` vài giây (giữ timeout riêng cho `getUpdates`).

### A-04 — Trung bình — gọi LLM không có timeout, lượt chat không có trần thời gian

- **Vị trí:** `app/agents/llm.py:43-51, 76-83`, `booking_graph/agents.py:71`, `supervisor.py:31`, `socketio_service.py:116-118`
- **Vấn đề:** timeparse/phrase/rewrite/state có `wait_for` 8s; supervisor và vòng tool thì không. SDK openai mặc định timeout 600s, retry 2. Azure treo → khách kẹt "đang nghĩ" nhiều phút, không có `error`, không hiện nút gọi tiệm.
- **Sửa:** `timeout≈20`, `max_retries=1` trong `build_chat_model`; `asyncio.timeout(...)` quanh `run_turn`.

### A-05 — Trung bình — mạng chập giữa lượt chat: mất câu trả lời, UI kẹt

- **Vị trí:** `socketio_service.py:118` (`emit(..., room=sid)`), `frontend/src/hooks/useAgentStream.ts:72-93`
- **Vấn đề:** sự kiện gửi tới sid cũ; reconnect có sid mới; `onDisconnect` không reset `phase`; màn chat không nạp lại lịch sử. Khách nói "ừ", lịch đã ghi, đổi 4G→wifi → kẹt "đang nghĩ" và không biết đã đặt.
- **Sửa:** emit vào room `user_{id}` kèm `turn_id`, hoặc reconnect thì nạp lại tin hôm nay qua REST và reset `phase`.

### A-06 — Trung bình — hàng đợi offline của frontend tái hiện chắc chắn REL-01

- **Vị trí:** `frontend/src/hooks/useAgentStream.ts:76`
- **Vấn đề:** có mạng lại thì emit **mọi** tin chờ cùng lúc; python-socketio mặc định `async_handlers=True` → các `run_turn` chạy song song, ghi đè `pending_confirmation` của nhau, hai câu trả lời trộn vào một bong bóng.
- **Sửa:** gửi tuần tự (đợi `complete`/`error`) hoặc gộp tin chờ thành một; server vẫn nên khoá theo user như REL-01.

### A-07 — Trung bình — `pending_confirmation` sống sót sau lượt chat lỗi

- **Vị trí:** `booking_graph/tools.py:265, 304-307`, `events.py:128-132`, `confirm.py:115`
- **Vấn đề:** tool ghi pending ngay khi được gọi. LLM lỗi ở vòng sau → khách thấy "Máy đang bận…", lịch sử không ghi, nhưng pending còn 10 phút. Khách gõ lại "ok đặt giúp chị chiều mai" → đi thẳng `confirm`, ghi lịch theo giờ khách **chưa từng được hỏi**. Bug `is_affirmative` làm nặng thêm.
- **Sửa:** nhánh exception / `answer` rỗng của `run_turn` thì `set_pending(None)`.

### A-08 — Trung bình — dependency lõi không pin, build lại image không tái lập được

- **Vị trí:** `requirements.txt:41-50` (`langgraph>=`, `langchain-openai>=`, `langchain>=`, `langfuse>=`, `python-socketio>=`), `Dockerfile` cài từ `requirements.txt`, bỏ qua `uv.lock`
- **Vấn đề:** checklist ngày phát hành có bước build lại image → có thể kéo LangChain/LangGraph bản lớn mới, đổi `astream_events`/tag/callback → mất stream hoặc trace im lặng, đúng loại lỗi fail-soft không làm test nào đỏ.
- **Sửa:** pin chính xác, hoặc xuất `requirements.lock` từ `uv.lock` và cài từ đó.

### A-09 — Trung bình — `AGENTS.md` lỗi thời và mâu thuẫn code [đã kiểm]

- `AGENTS.md` ghi "Context block goes first after system prompt" — ngược với `CONTEXT.md` #9 (System → summary + slots → lịch sử → bối cảnh → tin mới, sửa 2026-09-13). Một agent làm theo `AGENTS.md` sẽ tái tạo đúng lỗi đã sửa.
- Còn lại: "Backend only, 281 tests" (đã có Telegram + frontend), "5 read-only tools" (booking có 7), "`propose_appointment` holds a slot" (không giữ gì trong DB — gốc của A-01), "LLM failure at startup is a warning" (lifespan không còn khởi tạo LLM).
- **Sửa:** cập nhật `AGENTS.md`, trỏ thẳng sang `CONTEXT.md` cho thứ tự message.

### A-10 — Trung bình — logic xếp lịch nằm ở tool agent, vi phạm "mọi logic đặt lịch ở `app/services/`"

- **Vị trí:** `app/agents/booking_graph/tools.py:191-265` (`propose_appointment`: kiểm sở hữu, `already_yours`, `start in free`, chọn 3 mốc gần nhất; gọi thẳng `service.repo.find_active_at`)
- **Sửa:** `AppointmentService.check_proposal(user, start, replaces_id)` trả kết quả có kiểu (ok / already_yours / unavailable+nearest / not_owned); tool chỉ serialize. Telegram/REST dùng lại được.
- **Nguồn:** `architecture-solid-principles-check` (medium), `architecture-cyclomatic-complexity`, reviewer kiến trúc.

### Các mục thấp

| ID | Vấn đề | Vị trí |
|---|---|---|
| A-11 | [đã kiểm] Admin dời lịch của khách → lịch mới tạo dưới tài khoản **admin** (`create(user, ...)` với `user` là người thao tác) | `app/services/appointment.py:122-128` |
| A-12 | Lặp: bảng tên thứ tiếng Việt + quy ước chỉ số thứ ở 4 module; horizon 90 ngày ở 2 module và service không chặn | `timeparse.py:203`, `context.py:19`, `tools.py:88`, `services/shop.py:77`, `conversation_state.py:210` |
| A-13 | Phụ thuộc ngược: Telegram (bot "không AI") import formatter từ `booking_graph.context`; `services/` import `agents.llm` và `run_turn` | `app/telegram/handlers.py:14`, `notify.py:5`, `services/conversation_state.py:12`, `services/socketio_service.py:6` |
| A-14 | Router/service đi tắt: router auth gọi thẳng `UserRepository`; quy tắc "đặt hộ khách" nằm ở router; `cancel` chép lại logic của `own_booked`; Shop/Conversation/RateLimit truy vấn collection trực tiếp | `routers/auth.py:114`, `routers/appointments.py:38`, `services/appointment.py:136`, `services/shop.py:17` |
| A-15 | Code chết: `ConversationRepository` không ai dùng; `BaseRepository` có CRUD tổng quát (`update`, `delete_many`, `bulk_create`…) không ai gọi — mời người sau đi vòng qua quy tắc domain (hủy = lật status) | `app/repositories/conversation.py:7`, `base.py:41` |
| A-16 | `notifications` là singleton cấp module, service import trực tiếp; `SocketIOService` gánh transport + xác thực + rate limit + câu chữ + điều phối lượt | `services/notifications.py:56`, `socketio_service.py:18` |
| A-17 | Chuỗi khách thấy sai xưng hô: "Cô chú gọi giúp con…", "lịch **bạn** đã đặt" ; test prompt không quét các file này | `main.py:168`, `services/appointment.py:69`, `telegram/handlers.py:21` |
| A-18 | Hai lượt nén state của cùng khách chạy song song → tốn LLM gấp đôi, `covers_until` có thể lùi | `booking_graph/events.py:23-27`, `services/conversation_state.py:127-195` |
| A-19 | Shutdown không chờ các `run_turn` đang chạy → lượt "ừ" đúng lúc deploy có thể ghi lịch mà không lưu lịch sử/báo khách | `main.py:101-121` |
| A-20 | Đặt tên: `socket_notifier.py` vs `telegram/notify.py`; `socketio_service.py` là transport nhưng nằm trong `services/`; `booking_graph/agents.py` tên không nói gì | `app/services/`, `app/agents/booking_graph/agents.py` |

### Điểm mạnh kiến trúc

- Chống trùng giờ bằng partial unique index ở DB; repository đổi `DuplicateKeyError` → `SlotTakenError`.
- Agent không có tool ghi; lịch ghi từ giá trị trong Mongo, `user_id` không do model điều khiển.
- REST, Telegram, agent cùng đi qua `AppointmentService`; báo tin qua một cửa `NotificationService`.
- Fail-soft có chủ đích: Mongo chết thì dừng hẳn, Telegram lỗi thì app vẫn chạy; tách `/health/` và `/health/ready`.
- Refresh token opaque, chỉ lưu hash, cookie `HttpOnly` + `SameSite=strict`, xoay vòng có phát hiện dùng lại; access token chỉ trong bộ nhớ.
- Bộ test dày, có test hồi quy cho các bẫy then chốt.

---

## Trạng thái các ID cũ

| ID | Trạng thái |
|---|---|
| SEC-01 | Còn — phạm vi rộng hơn mô tả, xem S-04 |
| SEC-02 | Đã xong |
| SEC-03 | Còn (Mongo không auth, mongo-express, cổng mở) |
| SEC-04 | Còn — gộp vào S-03 |
| SEC-05 | Còn — **mức độ cũ sai, admin cũng dính**, xem S-01 |
| REL-01 | Còn — A-06 cho cách tái hiện chắc chắn |
| REL-06 | Còn — thêm khía cạnh bảo mật S-12 |
| INFO-02 | Còn — thêm lý do S-16 |
| DEP-01 | Còn — xem S-18 |
| Bug `is_affirmative` | Còn — làm nặng thêm A-07 |

## Giới hạn của review này

- Không chạy `pip-audit`/`npm audit` (cần mạng); CVE ở S-18 dựa trên hiểu biết, chưa xác nhận bằng công cụ.
- Không dựng app để khai thác thật. Ngoại lệ: chữ ký JWT bằng khoá rỗng (S-03) đã chạy thử trong `.venv`.
- Chỉ các phát hiện high của swarm qua bước kiểm chứng đối kháng; mục **[đã kiểm]** được đối chiếu thêm bằng tay. Các mục còn lại là phát hiện của một agent, có trích file:dòng nhưng chưa có người thứ hai xác nhận.
- S-12 (sid trong log), A-03 (Telegram bị chặn ở VN), A-04 (timeout mặc định SDK), A-06 (`async_handlers`) dựa vào hành vi mặc định của thư viện/môi trường, chưa chạy thật.
