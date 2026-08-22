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
