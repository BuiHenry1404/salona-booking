# App đặt lịch nail–tóc

Thiết kế: [`docs/superpowers/specs/2026-08-06-booking-nail-toc/`](docs/superpowers/specs/2026-08-06-booking-nail-toc/README.md)
Chạy và dùng: [`RUNBOOK.md`](RUNBOOK.md) — có sẵn tài khoản test, cấu hình Azure, Langfuse.

## Chạy local

```bash
cp .env.example .env      # điền MONGO_URI, JWT_SECRET, API_KEY
docker compose up -d mongo
uvicorn main:app --reload
```

Swagger: http://localhost:8000/docs

## Giao diện web

```bash
cd frontend && npm install && npm run dev
```

Chi tiết xem [`frontend/README.md`](frontend/README.md). Nhớ thêm
`http://localhost:5173` vào `ALLOWED_ORIGINS` trong `.env`, nếu không mọi
request từ giao diện đều bị CORS chặn.

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

Nếu về sau thật sự cần nhiều worker, phương án là tách bot thành tiến trình riêng và thêm cờ `RUN_TELEGRAM_POLLER` để lifespan của web không khởi động vòng lặp poll.

### Lấy chat_id của chủ tiệm

Tạo bot qua [@BotFather](https://t.me/BotFather), lấy token, điền vào `TELEGRAM_BOT_TOKEN` trong `.env`. Nhắn bot một câu bất kỳ, rồi mở:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Số trong `result[0].message.chat.id` chính là chat_id. Điền vào `TELEGRAM_ADMIN_CHAT_IDS` (nhiều người thì phân cách bằng dấu phẩy). Bot **chỉ** trả lời những chat_id trong danh sách này — nó đọc được tên và số điện thoại khách, nên đây là ranh giới bảo mật, không phải tiện ích. Thiếu `TELEGRAM_BOT_TOKEN` thì bot không khởi động, app vẫn chạy bình thường.


## Cấu hình

`app/core/config.py` **không giữ giá trị mặc định nào** — mọi thiết lập nằm ở `.env`. Thiếu một khoá bắt buộc thì app không khởi động được, kèm tên trường bị thiếu. Thêm thiết lập mới phải thêm vào cả `config.py`, `.env.example` và `.env`.

## Test

```bash
docker compose up -d mongo
pytest -v
```

Cần MongoDB chạy ở `localhost:27017`. Test dùng database riêng `chatbot_test_db` và tự dọn sau mỗi test.
