# Task 4 · Vòng lặp long polling

> Thuộc plan [Bot Telegram](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/telegram/bot.py`, `tests/test_telegram_bot.py`
- Modify: `app/telegram/__init__.py`, `main.py`, `README.md`

**Interfaces:**
- Consumes: `TelegramClient`, `is_configured` (task 2), `TelegramHandlers` (task 3)
- Produces:
  - `TelegramBot(db).run() -> None` — vòng lặp vô hạn
  - `TelegramBot(db).stop() -> None`
  - `start_bot(db) -> asyncio.Task | None` — trả `None` nếu chưa cấu hình

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_telegram_bot.py`:

```python
import asyncio

import pytest

from app.telegram.bot import TelegramBot, start_bot

pytestmark = pytest.mark.asyncio


class FakeClient:
    """Trả về vài lô update rồi rỗng mãi."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.offsets = []
        self.closed = False

    async def get_updates(self, offset, timeout=25):
        self.offsets.append(offset)
        await asyncio.sleep(0)
        return self.batches.pop(0) if self.batches else []

    async def aclose(self):
        self.closed = True


class SpyHandlers:
    def __init__(self):
        self.seen = []

    async def handle_update(self, update):
        self.seen.append(update["update_id"])


async def run_briefly(bot, seconds=0.2):
    task = asyncio.create_task(bot.run())
    await asyncio.sleep(seconds)
    bot.stop()
    await asyncio.wait_for(task, timeout=2)


async def test_every_update_reaches_the_handlers(test_db):
    client = FakeClient([[{"update_id": 1}, {"update_id": 2}], [{"update_id": 3}]])
    handlers = SpyHandlers()
    bot = TelegramBot(test_db, client=client, handlers=handlers, idle_sleep=0.01)

    await run_briefly(bot)
    assert handlers.seen == [1, 2, 3]


async def test_offset_advances_past_the_highest_update_id(test_db):
    """Không tăng offset thì Telegram gửi lại cùng update mãi mãi."""
    client = FakeClient([[{"update_id": 10}], [{"update_id": 12}]])
    bot = TelegramBot(test_db, client=client, handlers=SpyHandlers(), idle_sleep=0.01)

    await run_briefly(bot)
    assert client.offsets[0] == 0
    assert 11 in client.offsets
    assert 13 in client.offsets


async def test_a_failing_handler_does_not_kill_the_loop(test_db):
    class Broken:
        def __init__(self):
            self.count = 0

        async def handle_update(self, update):
            self.count += 1
            raise RuntimeError("hỏng")

    client = FakeClient([[{"update_id": 1}], [{"update_id": 2}]])
    handlers = Broken()
    bot = TelegramBot(test_db, client=client, handlers=handlers, idle_sleep=0.01)

    await run_briefly(bot)
    assert handlers.count == 2


async def test_stop_closes_the_client(test_db):
    client = FakeClient([])
    bot = TelegramBot(test_db, client=client, handlers=SpyHandlers(), idle_sleep=0.01)
    await run_briefly(bot)
    assert client.closed is True


async def test_start_bot_returns_none_when_not_configured(test_db, monkeypatch):
    monkeypatch.setattr("app.telegram.bot.is_configured", lambda: False)
    assert start_bot(test_db) is None
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.telegram.bot'`

- [ ] **Step 3: Viết `app/telegram/bot.py`**

```python
import asyncio
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.logging import get_logger
from app.telegram.client import TelegramClient, is_configured
from app.telegram.handlers import TelegramHandlers

logger = get_logger(__name__)

POLL_TIMEOUT_SECONDS = 25
ERROR_BACKOFF_SECONDS = 5.0


class TelegramBot:
    """Vòng lặp getUpdates.

    Chọn long polling thay webhook vì không cần domain public, không cần HTTPS,
    chạy được sau NAT — hợp với việc deploy một tiệm nhỏ.

    CẢNH BÁO VẬN HÀNH: Telegram chỉ chấp nhận MỘT kết nối getUpdates cho mỗi bot
    token. Chạy nhiều worker nghĩa là nhiều tiến trình cùng poll, chúng đá nhau
    và Telegram trả 409 Conflict không dứt — bot lúc nhận được tin, lúc không.
    """

    def __init__(self, db: AsyncIOMotorDatabase, client=None, handlers=None,
                 idle_sleep: float = 1.0):
        self.client = client or TelegramClient()
        self.handlers = handlers or TelegramHandlers(db, self.client)
        self.idle_sleep = idle_sleep
        self._running = False
        self._offset = 0

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("telegram_bot_started")
        try:
            while self._running:
                try:
                    updates = await self.client.get_updates(
                        offset=self._offset, timeout=POLL_TIMEOUT_SECONDS
                    )
                except Exception as exc:
                    logger.warning("telegram_poll_failed", extra={"error": str(exc)})
                    await asyncio.sleep(ERROR_BACKOFF_SECONDS)
                    continue

                if not updates:
                    await asyncio.sleep(self.idle_sleep)
                    continue

                for update in updates:
                    # Offset phải tiến kể cả khi xử lý lỗi, nếu không Telegram
                    # gửi lại cùng update mãi mãi.
                    self._offset = max(self._offset, update.get("update_id", 0) + 1)
                    try:
                        await self.handlers.handle_update(update)
                    except Exception as exc:
                        logger.warning("telegram_handler_failed", extra={"error": str(exc)})
        finally:
            await self.client.aclose()
            logger.info("telegram_bot_stopped")


def start_bot(db: AsyncIOMotorDatabase) -> Optional[asyncio.Task]:
    """Khởi động bot nếu có token. Thiếu token thì app chạy bình thường, không bot."""
    if not is_configured():
        logger.info("telegram_bot_disabled")
        return None
    return asyncio.create_task(TelegramBot(db).run())
```

- [ ] **Step 4: Xuất API trong `app/telegram/__init__.py`**

```python
from app.telegram.bot import TelegramBot, start_bot

__all__ = ["TelegramBot", "start_bot"]
```

- [ ] **Step 5: Nối vào lifespan của FastAPI**

Trong `main.py`, tìm hàm lifespan và thêm sau `await ensure_indexes(db)`:

```python
    from app.telegram import start_bot
    telegram_task = start_bot(db)
```

Và trong phần dọn dẹp (sau `yield`):

```python
    if telegram_task:
        telegram_task.cancel()
```

- [ ] **Step 6: Ghi ràng buộc 1 worker vào README**

Thêm vào `README.md`, ngay dưới phần "Chạy production":

```markdown
### Bắt buộc: đúng 1 worker

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Không truyền `--workers`, không dùng `gunicorn -w N`, không scale container lên nhiều replica.

Bot Telegram dùng long polling, mà Telegram chỉ chấp nhận **một** kết nối `getUpdates` cho mỗi bot token. Mỗi worker là một tiến trình riêng chạy lại toàn bộ lifespan, nên N worker nghĩa là N tiến trình cùng poll: chúng đá nhau và Telegram trả **409 Conflict** liên tục — bot lúc nhận được tin, lúc không.

Nếu về sau thật sự cần nhiều worker, phương án là tách bot thành tiến trình riêng và thêm cờ `RUN_TELEGRAM_POLLER` để lifespan của web không khởi động vòng lặp poll.

### Lấy chat_id của chủ tiệm

Nhắn bot một câu bất kỳ, rồi mở:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Số trong `result[0].message.chat.id` chính là chat_id. Điền vào `TELEGRAM_ADMIN_CHAT_IDS` (nhiều người thì phân cách bằng dấu phẩy). Bot **chỉ** trả lời những chat_id trong danh sách này — nó đọc được tên và số điện thoại khách.
```

- [ ] **Step 7: Chạy test để xác nhận pass**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: PASS (5 passed)

- [ ] **Step 8: Kiểm tra app khởi động được khi không có token**

Run: `python -c "import main; print('ok')"`
Expected: in `ok`, không treo

- [ ] **Step 9: Commit**

```bash
git add app/telegram main.py README.md tests/test_telegram_bot.py
git commit -m "feat: Telegram long-polling loop wired into app lifespan"
```
