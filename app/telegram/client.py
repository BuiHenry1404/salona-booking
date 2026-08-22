from typing import List, Optional, Set

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://api.telegram.org"

# Bàn phím cố định hiện sẵn dưới ô nhập. Chủ tiệm lớn tuổi chỉ việc bấm,
# không phải nhớ gõ /homnay.
MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "Hôm nay"}, {"text": "Ngày mai"}],
        [{"text": "Tôi đang bận"}, {"text": "Tôi rảnh rồi"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def duration_keyboard() -> dict:
    """Bốn lựa chọn khớp đúng với bốn nút trên web app — chủ tiệm không phải
    học hai kiểu thao tác."""
    return {
        "inline_keyboard": [
            [{"text": "15 phút", "callback_data": "busy:15"},
             {"text": "30 phút", "callback_data": "busy:30"}],
            [{"text": "1 tiếng", "callback_data": "busy:60"},
             {"text": "2 tiếng", "callback_data": "busy:120"}],
        ]
    }


def is_configured() -> bool:
    return bool(settings.telegram_bot_token)


def admin_chat_ids() -> Set[int]:
    """Chỉ những chat_id này được bot trả lời. Bot đọc được tên và số điện thoại
    khách nên đây là ranh giới bảo mật, không phải tiện ích."""
    ids: Set[int] = set()
    for chunk in (settings.telegram_admin_chat_ids or "").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.add(int(chunk))
    return ids


class TelegramClient:
    """Gọi thẳng Bot API. Không dùng python-telegram-bot hay aiogram: chỉ có 4 nút
    và 2 loại thông báo, bàn phím lẫn nút inline đều chỉ là JSON."""

    def __init__(self, token: Optional[str] = None, http: Optional[httpx.AsyncClient] = None):
        self.token = token or (
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token else ""
        )
        self._http = http
        self._owns_http = http is None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    async def _post(
        self, method: str, payload: dict, timeout: float = 30.0, swallow: bool = True
    ) -> Optional[dict]:
        try:
            response = await self.http.post(self._url(method), json=payload, timeout=timeout)
            body = response.json()
        except Exception as exc:
            logger.warning("telegram_call_failed", extra={"method": method, "error": str(exc)})
            if not swallow:
                raise
            return None
        if not body.get("ok"):
            logger.warning("telegram_call_rejected",
                           extra={"method": method, "description": body.get("description")})
            return None
        return body

    async def send_message(
        self, chat_id: int, text: str, keyboard: Optional[dict] = None
    ) -> None:
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = keyboard
        await self._post("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def get_updates(self, offset: int, timeout: int = 25) -> List[dict]:
        """Khác `send_message` ở chỗ CÓ ném lỗi ra ngoài.

        Gửi tin thì nuốt lỗi, vì Telegram chết không được làm hỏng việc đặt lịch.
        Nhưng poll mà cũng nuốt thì mạng đứt sẽ trả về danh sách rỗng — vòng lặp
        hiểu nhầm là "không có tin mới", chạy tiếp với nhịp nghỉ 1 giây và nhánh
        lùi 5 giây trong `bot.py` thành code chết.
        """
        body = await self._post(
            "getUpdates",
            {"offset": offset, "timeout": timeout,
             "allowed_updates": ["message", "callback_query"]},
            timeout=timeout + 10,
            swallow=False,
        )
        return (body or {}).get("result", [])
