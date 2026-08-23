# Lên prod — việc cần làm

Danh sách này gom từ `REPO_AUDIT.md` (mục P1–P3) và mục "Chưa có" trong
`CONTEXT.md`. Cách chạy nằm ở [`RUNBOOK.md`](RUNBOOK.md).

Cập nhật: 2026-08-23

## Đã xong

- [x] REL-02 — validate giờ mở cửa (`22:99` không lưu được nữa)
- [x] SEC-02 — trần chat 30 tin/giờ mỗi khách, kèm "chờ bao lâu"
- [x] INFO-01 — dẹp manager LLM autogen chết
- [x] `HEALTHCHECK`, `restart: unless-stopped`, `.dockerignore`

## Ràng buộc cứng — đọc trước khi thiết kế hạ tầng

- **Đúng 1 worker.** Không `--workers`, không `gunicorn -w N`, không nhiều
  replica. Bot Telegram dùng long polling và Telegram chỉ cho một kết nối
  `getUpdates` mỗi token. Hệ quả: **mỗi lần deploy đều có downtime.**
- **Cấu hình chỉ đọc từ file `.env`**, không đọc biến môi trường của shell.
  Khối `environment:` trong compose không có tác dụng với app.
- **Nút micro chỉ chạy trên HTTPS** (Web Speech API). Không HTTPS thì mất
  chức năng, không chỉ mất bảo mật.

---

## Bước 1 — Chặn đường, không làm không lên được

### 1.1 Tách compose prod khỏi dev

`docker-compose.yml` hiện tại **không được dùng cho prod**.

- [ ] Tạo `docker-compose.prod.yml`
- [ ] **Bỏ hẳn `mongo-express`** — cổng 8081, `admin`/`admin` hardcode, ai vào
      cũng đọc và sửa được dữ liệu khách
- [ ] **Bỏ `ports: 27017:27017`** của mongo — chỉ để app trong cùng network gọi
- [ ] **Bật auth cho Mongo** (`MONGO_INITDB_ROOT_USERNAME/PASSWORD`), cập nhật
      `MONGO_URI`
- [ ] Bỏ `volumes: .:/app` và `--reload` — prod chạy code trong image, không
      mount source
- [ ] Giữ `ulimits.nofile` 64000 (WiredTiger cần)

### 1.2 HTTPS + reverse proxy

- [ ] nginx (hoặc Caddy) đứng trước app
- [ ] Chứng chỉ Let's Encrypt, tự gia hạn
- [ ] Proxy WebSocket cho `/socket.io/` (`Upgrade` + `Connection` header) —
      thiếu là chat chết, REST vẫn chạy nên rất dễ bỏ sót
- [ ] Kiểm nút micro hoạt động sau khi có HTTPS

### 1.3 Sao lưu Mongo

Mất Mongo là fail-hard duy nhất của cả hệ.

- [ ] `mongodump` theo lịch (cron/systemd timer), đẩy ra ngoài máy chủ
- [ ] **Thử phục hồi thật một lần** — bản sao lưu chưa restore được là bản sao
      lưu chưa tồn tại
- [ ] Ghi lại chỗ để bản sao lưu và lệnh phục hồi vào `RUNBOOK.md`

### 1.4 `.env` của prod

- [ ] `ENV=prod` → tự tắt `/docs` và `/redoc`
- [ ] `DEBUG=false`
- [ ] **`JWT_SECRET` mới**, sinh ngẫu nhiên, khác hoàn toàn `.env` dev
- [ ] `COOKIE_SECURE=true` (cần HTTPS ở bước 1.2 trước)
- [ ] `ALLOWED_ORIGINS` = đúng domain thật, **không để `*`**
- [ ] `LOG_LEVEL=INFO`
- [ ] `MONGO_URI` trỏ Mongo có auth
- [ ] Khoá quyền file: `chmod 600`, không commit, không nằm trong image

### 1.5 SEC-01 — Socket.IO bỏ qua `token_version`

Đổi mật khẩu thu hồi token trên HTTP, nhưng socket đang mở vẫn sống tới khi JWT
hết hạn.

- [ ] Thêm kiểm `token_version` vào `_authenticate` của `SocketIOService`
- [ ] Test hồi quy: đổi mật khẩu → socket cũ bị đá

### 1.6 Dọn bề mặt thừa (INFO-02)

- [ ] Gỡ `/static/socketio_test.html` và endpoint `/socket-info` ở prod (gắn
      sau `settings.env != "prod"` như `/docs` đã làm)

### 1.7 Frontend

- [ ] `npm run build` → phục vụ `dist/` qua nginx
- [ ] `VITE_API_BASE` trỏ domain thật
- [ ] `VITE_SHOP_PHONE` đúng số của tiệm

---

## Bước 2 — Nên làm sớm sau khi lên

- [ ] **CI** — chưa có `.github/` nào. Chạy pytest + `npm test` + `npm run build`
      + `pip-audit` mỗi lần push
- [ ] **DEP-01** — `python-jose` đã bỏ hoang và có CVE → thay bằng `PyJWT`; nâng
      FastAPI/uvicorn/httpx theo minor
- [ ] **Throttle toàn cục theo IP** — để nginx làm, tốt hơn tầng ứng dụng. Nếu
      làm ở app thì đọc kỹ 4 điều kiện trong `CONTEXT.md` (đặc biệt:
      `X-Forwarded-For` chỉ tin khi có proxy thật, và miễn trừ `/health`)
- [ ] **REL-01** — hai lượt chat song song của cùng một khách giẫm nhau
- [ ] **SEC-04** — chặn wildcard CORS khi `ENV=prod`
- [ ] **REL-03** — clamp `busy:N` của Telegram về 5–480 phút

## Bước 3 — Khi rảnh

- [ ] REL-05 — `conversations` phình vô hạn, chưa có TTL. Vá bằng
      `{"messages": {"$slice": -50}}` khi cần, không phải tách bảng
- [ ] REL-04 — sanitize `VITE_SHOP_PHONE` trước khi đưa vào `tel:`
- [ ] REL-06 — log path thay vì full URL (query string đang vào log ở mức INFO)
- [ ] Đặt lại mật khẩu admin qua Telegram — xem
      `docs/superpowers/specs/2026-08-15-admin-password-reset-telegram-design.md`
      (`NOTE.md` ghi đường dẫn cũ `specs/…`, không còn đúng)
- [ ] Avatar chatbot — mockup đang trỏ `ai-avatar.jpg` không có trong git

---

## Checklist ngày phát hành

- [ ] `pytest` xanh toàn bộ
- [ ] `cd frontend && npm test && npm run build` xanh
- [ ] **Build lại image app** — `docker compose -f docker-compose.prod.yml build`.
      Image cũ không có code mới, đây là chỗ dễ sập bẫy nhất
- [ ] Sao lưu Mongo **ngay trước** khi deploy
- [ ] Deploy (chấp nhận downtime — 1 worker)
- [ ] `GET /api/v1/health/` trả 200 — **nhớ dấu `/` cuối**, thiếu là 307
- [ ] `GET /api/v1/health/ready` trả 200
- [ ] Log khởi động **không có dòng ERROR nào**
- [ ] Đăng nhập bằng tài khoản thật trên domain thật
- [ ] Chat thử một lượt đặt lịch đầu-cuối, thấy lịch trong DB
- [ ] Bấm nút micro — chứng minh HTTPS đã đúng
- [ ] Mở `/docs` → phải 404 (chứng minh `ENV=prod` đã ăn)
- [ ] Mở cổng 8081 → phải không truy cập được (mongo-express đã bỏ)
- [ ] Thử `mongosh` từ ngoài vào 27017 → phải bị từ chối

## Sau khi lên

- [ ] Theo dõi chi phí LLM vài ngày đầu (Langfuse, nếu bật)
- [ ] Xem lại `CHAT_MAX_PER_HOUR=30` có chặn nhầm khách thật không
- [ ] Kiểm bản sao lưu đầu tiên chạy đúng lịch

---

## Cạm bẫy đã gặp thật

- `docker compose down -v` **xoá volume** — mất sạch lịch khách và trace. Tắt
  thì dùng `down` hoặc `stop`, không kèm `-v`.
- Dựng lại stack Langfuse từ đầu là **mất đơn giá model** → mọi trace hiện 0đ.
  Lệnh seed lại nằm ở đầu `docker-compose.langfuse.yml`.
- Langfuse nặng (Postgres + ClickHouse + Redis + MinIO + web + worker). Cân nhắc
  có đáng chạy chung máy với app không.
- **Fail-soft che lỗi.** Hai lỗi nặng nhất tới giờ đều không làm test nào đỏ:
  Socket.IO mount sai đường dẫn, và timeout parser 2 giây. Chỗ nào nuốt lỗi thì
  phải có kịch bản chạy thật soi vào — `scripts/live_e2e.py` và
  `scripts/chat_scenarios.py` sinh ra vì lý do đó. **Chạy cả hai sau mỗi lần
  deploy.**
