# Task 12 · README vận hành

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: mọi thứ ở trên
- Produces: —

- [ ] **Step 1: Thay phần hướng dẫn chạy trong `README.md`**

Thay toàn bộ nội dung nói về template chatbot bằng:

````markdown
# App đặt lịch nail–tóc

Thiết kế: [`docs/superpowers/specs/2026-08-06-booking-nail-toc/`](docs/superpowers/specs/2026-08-06-booking-nail-toc/README.md)

## Chạy local

```bash
cp .env.example .env      # điền MONGO_URI, JWT_SECRET, API_KEY
docker compose up -d mongo
uvicorn main:app --reload
```

Swagger: http://localhost:8000/docs

## Tạo tài khoản chủ tiệm đầu tiên

Không có đăng ký tự do. Tài khoản admin đầu tiên phải tạo bằng tay:

```bash
python -c "
import asyncio
from app.infrastructure.database import create_mongodb_connection, ensure_indexes
from app.services.auth import AuthService
from app.core.config import settings

async def main():
    conn = await create_mongodb_connection(str(settings.mongo_uri), settings.mongo_db_name)
    db = conn.get_database()
    await ensure_indexes(db)
    await AuthService(db).create_user('0901234567', 'doi-mat-khau-nay', 'Chủ tiệm', role='admin')
    print('Đã tạo admin 0901234567')

asyncio.run(main())
"
```

## Chạy production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Đúng 1 worker.** Không truyền `--workers`, không dùng `gunicorn -w N`, không scale container lên nhiều replica: bot Telegram (Plan 3) dùng long polling, và Telegram chỉ chấp nhận một kết nối `getUpdates` cho mỗi bot token — nhiều tiến trình sẽ đá nhau và nhận 409 Conflict liên tục.

## Test

```bash
pytest -v
```

Cần MongoDB chạy ở `localhost:27017`. Test dùng database riêng `chatbot_test_db` và tự dọn sau mỗi test.
````

- [ ] **Step 2: Kiểm tra lệnh trong README chạy được**

Run: `docker compose up -d mongo && pytest -v`
Expected: PASS toàn bộ

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README for the booking app"
```

---
