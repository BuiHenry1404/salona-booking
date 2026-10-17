"""Test cho `bench.adapters.base` và `bench.adapters.a.AdapterA`.

Dựng một server Socket.IO giả (`python-socketio` `AsyncServer` gắn vào một
`aiohttp.web.Application`, cổng ngẫu nhiên) phát đúng kịch bản sự kiện mà
server thật phát ra, để kiểm adapter ghi lại chính xác mà không cần chạy
toàn bộ app thật (hộp đen).
"""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_stale_event_after_timeout_does_not_leak_into_next_turn(fake_server):
    """Review Focus #2 fix: lượt 1 timeout trong khi server vẫn đang xử lý
    (phát `complete` trễ); adapter phải ngắt/nối lại kết nối khi timeout nên
    `complete` trễ đó không thể tới và lẫn vào lượt 2."""
    turn_count = {"n": 0}

    async def on_send_message(sio, sid, data):
        turn_count["n"] += 1
        if turn_count["n"] == 1:

            async def late_reply() -> None:
                await asyncio.sleep(0.6)  # lâu hơn timeout_s=0.2 của lượt 1
                try:
                    await sio.emit("tool_started", {"name": "turn1_tool"}, to=sid)
                    await sio.emit("complete", {"answer": "turn1 answer"}, to=sid)
                except Exception:
                    pass  # kết nối cũ đã bị ngắt — đúng như kỳ vọng

            asyncio.create_task(late_reply())
        else:
            await sio.emit("tool_started", {"name": "turn2_tool"}, to=sid)
            await sio.emit("complete", {"answer": "turn2 answer"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record1 = await adapter.send(Turn(say="turn1"), index=0, timeout_s=0.2)
        record2 = await adapter.send(Turn(say="turn2"), index=1, timeout_s=2.0)
        # Để cho late_reply() (vẫn đang ngủ) kịp cố gửi vào kết nối cũ và
        # thất bại, tránh cảnh báo "task pending" khi test kết thúc.
        await asyncio.sleep(0.6)
    finally:
        await adapter.close()

    assert record1.error == "timeout"

    assert record2.answer == "turn2 answer"
    assert record2.tools == ["turn2_tool"]
    assert "turn1_tool" not in record2.tools
    assert all(
        e.data.get("answer") != "turn1 answer"
        for e in record2.events
        if e.type == "complete"
    )


@pytest.mark.asyncio
async def test_per_turn_state_resets(fake_server):
    turn_count = {"n": 0}

    async def on_send_message(sio, sid, data):
        turn_count["n"] += 1
        if turn_count["n"] == 1:
            await sio.emit("token", {"text": "a"}, to=sid)
            await sio.emit("tool_started", {"name": "tool1"}, to=sid)
            await sio.emit("complete", {"answer": "answer1"}, to=sid)
        else:
            await sio.emit("complete", {"answer": "answer2"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record1 = await adapter.send(Turn(say="turn1"), index=0, timeout_s=2.0)
        record2 = await adapter.send(Turn(say="turn2"), index=1, timeout_s=2.0)
    finally:
        await adapter.close()

    assert record1.answer == "answer1"
    assert record1.tools == ["tool1"]
    assert record1.first_token_ms is not None

    assert record2.answer == "answer2"
    assert record2.tools == []
    assert record2.first_token_ms is None
