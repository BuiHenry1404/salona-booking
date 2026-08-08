# Task 2 · Client Telegram bằng httpx

> Thuộc plan [Bot Telegram](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/telegram/__init__.py`, `app/telegram/client.py`, `tests/test_telegram_client.py`

**Interfaces:**
- Consumes: `settings.telegram_bot_token` (Plan 1 task 1)
- Produces:
  - `TelegramClient.send_message(chat_id: int, text: str, keyboard: dict | None = None) -> None`
  - `TelegramClient.answer_callback(callback_id: str, text: str = "") -> None`
  - `TelegramClient.get_updates(offset: int, timeout: int = 25) -> list[dict]`
  - `MAIN_KEYBOARD: dict`, `duration_keyboard() -> dict`
  - `is_configured() -> bool`, `admin_chat_ids() -> set[int]`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_telegram_client.py`:

```python
import httpx
import pytest

from app.telegram.client import (MAIN_KEYBOARD, TelegramClient, admin_chat_ids,
                                 duration_keyboard)

pytestmark = pytest.mark.asyncio


def client_with(handler):
    transport = httpx.MockTransport(handler)
    return TelegramClient(token="fake-token", http=httpx.AsyncClient(transport=transport))


async def test_send_message_posts_to_the_right_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    await client_with(handler).send_message(chat_id=123, text="xin chào")

    assert seen["url"].endswith("/botfake-token/sendMessage")
    assert seen["json"]["chat_id"] == 123
    assert seen["json"]["text"] == "xin chào"


async def test_send_message_attaches_the_keyboard():
    seen = {}

    def handler(request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    await client_with(handler).send_message(123, "chọn đi", keyboard=MAIN_KEYBOARD)
    assert seen["json"]["reply_markup"] == MAIN_KEYBOARD


async def test_network_failure_is_swallowed():
    """Telegram chết không được làm hỏng việc đặt lịch của khách."""

    def handler(request):
        raise httpx.ConnectError("mạng chết")

    await client_with(handler).send_message(123, "xin chào")  # không được ném


async def test_http_error_is_swallowed():
    def handler(request):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    await client_with(handler).send_message(123, "xin chào")


async def test_get_updates_returns_the_result_list():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    assert await client_with(handler).get_updates(offset=0) == [{"update_id": 7}]


async def test_get_updates_raises_so_the_loop_can_back_off():
    """Ngược với send_message: poll hỏng PHẢI ném ra ngoài.

    Nuốt lỗi ở đây thì mạng đứt trông giống hệt "không có tin mới", và nhánh lùi
    5 giây trong bot.py không bao giờ chạy — bot quay vòng 1 giây một lần suốt
    cả đợt sự cố.
    """
    def handler(request):
        raise httpx.ReadTimeout("hết giờ")

    with pytest.raises(httpx.ReadTimeout):
        await client_with(handler).get_updates(offset=0)


def test_main_keyboard_has_exactly_four_buttons():
    rows = MAIN_KEYBOARD["keyboard"]
    labels = [b["text"] if isinstance(b, dict) else b for row in rows for b in row]
    assert labels == ["Hôm nay", "Ngày mai", "Tôi đang bận", "Tôi rảnh rồi"]
    assert MAIN_KEYBOARD["resize_keyboard"] is True


def test_duration_keyboard_has_four_choices_matching_the_web_app():
    buttons = [b for row in duration_keyboard()["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == ["15 phút", "30 phút", "1 tiếng", "2 tiếng"]
    assert [b["callback_data"] for b in buttons] == ["busy:15", "busy:30", "busy:60", "busy:120"]


def test_admin_chat_ids_parses_a_comma_separated_list(monkeypatch):
    from app.telegram import client as mod

    monkeypatch.setattr(mod.settings, "telegram_admin_chat_ids", "111, 222 ,333")
    assert admin_chat_ids() == {111, 222, 333}


def test_admin_chat_ids_is_empty_when_unset(monkeypatch):
    from app.telegram import client as mod

    monkeypatch.setattr(mod.settings, "telegram_admin_chat_ids", "")
    assert admin_chat_ids() == set()


def test_admin_chat_ids_ignores_rubbish(monkeypatch):
    from app.telegram import client as mod

    monkeypatch.setattr(mod.settings, "telegram_admin_chat_ids", "111,khong-phai-so,222")
    assert admin_chat_ids() == {111, 222}
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_telegram_client.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.telegram'`

- [ ] **Step 3: Viết `app/telegram/client.py`**

```python
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
```

- [ ] **Step 4: Tạo `app/telegram/__init__.py`**

```python
```

(file rỗng — nội dung export thêm ở task 4)

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_telegram_client.py -v`
Expected: PASS (11 passed)

- [ ] **Step 6: Commit**

```bash
git add app/telegram tests/test_telegram_client.py
git commit -m "feat: Telegram Bot API client over httpx with button keyboards"
```
