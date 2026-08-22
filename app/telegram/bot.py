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
