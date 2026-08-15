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


## Cấu hình

`app/core/config.py` **không giữ giá trị mặc định nào** — mọi thiết lập nằm ở `.env`. Thiếu một khoá bắt buộc thì app không khởi động được, kèm tên trường bị thiếu. Thêm thiết lập mới phải thêm vào cả `config.py`, `.env.example` và `.env`.

## Test

```bash
docker compose up -d mongo
pytest -v
```

Cần MongoDB chạy ở `localhost:27017`. Test dùng database riêng `chatbot_test_db` và tự dọn sau mỗi test.
