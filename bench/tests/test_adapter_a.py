"""Test cho `bench.adapters.base` và `bench.adapters.a.AdapterA`.

Dựng một server Socket.IO giả (`python-socketio` `AsyncServer` gắn vào một
`aiohttp.web.Application`, cổng ngẫu nhiên) phát đúng kịch bản sự kiện mà
server thật phát ra, để kiểm adapter ghi lại chính xác mà không cần chạy
toàn bộ app thật (hộp đen).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
import socketio
from aiohttp import web

from bench.adapters.a import AdapterA
from bench.scenario import Turn
from bench.seed import Customer

# Gọi mỗi khi client gửi "send_message": (sio, sid, data) -> None.
SendMessageHandler = Callable[[socketio.AsyncServer, str, dict], Awaitable[None]]


class FakeServer:
    """Server Socket.IO giả dùng `AsyncServer` gắn vào `aiohttp.web.Application`."""

    def __init__(self, on_send_message: SendMessageHandler) -> None:
        self.sio = socketio.AsyncServer(async_mode="aiohttp")
        self.app = web.Application()
        self.sio.attach(self.app)
        self._on_send_message = on_send_message
        self._runner: web.AppRunner | None = None

        @self.sio.event
        async def connect(sid, environ, auth):
            # Server thật phát "connected" ngay sau khi kết nối (trước khi có
            # lượt nào); adapter chưa có lượt đang mở nên sự kiện này không
            # gắn vào TurnRecord nào — các test dưới phát "connected" (và sự
            # kiện lạ khác) *trong* lượt để kiểm việc ghi lại sự kiện bất kỳ.
            pass

        @self.sio.event
        async def send_message(sid, data):
            await self._on_send_message(self.sio, sid, data)

    async def start(self) -> str:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


@pytest.fixture
async def fake_server():
    servers: list[FakeServer] = []

    async def _start(on_send_message: SendMessageHandler) -> str:
        server = FakeServer(on_send_message)
        servers.append(server)
        return await server.start()

    yield _start

    for server in servers:
        await server.stop()


def _customer() -> Customer:
    return Customer(id="c1", phone="0987654321", name="Khách test", token="tok-123")


@pytest.mark.asyncio
async def test_records_tokens_tools_and_answer(fake_server):
    async def on_send_message(sio, sid, data):
        await sio.emit("turn_started", {}, to=sid)
        await sio.emit("token", {"text": "Dạ "}, to=sid)
        await sio.emit("token", {"text": "chào "}, to=sid)
        await sio.emit("tool_started", {"name": "parse_time"}, to=sid)
        await sio.emit("tool_finished", {"name": "parse_time", "ok": True}, to=sid)
        await sio.emit("complete", {"answer": "Dạ…"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(Turn(say="Chào shop"), index=0, timeout_s=2.0)
    finally:
        await adapter.close()

    assert record.answer == "Dạ…"
    assert record.tools == ["parse_time"]
    assert record.first_token_ms is not None
    assert record.complete_ms is not None
    assert record.complete_ms >= record.first_token_ms
    assert record.error is None
    event_types = [e.type for e in record.events]
    assert event_types == [
        "turn_started",
        "token",
        "token",
        "tool_started",
        "tool_finished",
        "complete",
    ]


@pytest.mark.asyncio
async def test_intent_agree_sends_u(fake_server):
    received: list[dict] = []

    async def on_send_message(sio, sid, data):
        received.append(data)
        await sio.emit("complete", {"answer": "ok"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(Turn(intent="agree"), index=0, timeout_s=2.0)
    finally:
        await adapter.close()

    assert received[0]["message"] == "ừ"
    assert record.customer_text == "ừ"


@pytest.mark.asyncio
async def test_turn_timeout_is_recorded(fake_server):
    async def on_send_message(sio, sid, data):
        # Không bao giờ phát complete/error — mô phỏng lượt treo.
        return

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(Turn(say="xin chào"), index=0, timeout_s=0.5)
    finally:
        await adapter.close()

    assert record.error == "timeout"


@pytest.mark.asyncio
async def test_unknown_events_are_kept_and_rate_limited_is_error(fake_server):
    async def on_send_message(sio, sid, data):
        await sio.emit("connected", {"full_name": "Khách test"}, to=sid)
        await sio.emit("weird_event", {"foo": "bar"}, to=sid)
        await sio.emit("rate_limited", {"message": "chờ"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(Turn(say="xin chào"), index=0, timeout_s=2.0)
    finally:
        await adapter.close()

    event_types = [e.type for e in record.events]
    assert "connected" in event_types
    assert "weird_event" in event_types
    assert record.error == "chờ"
