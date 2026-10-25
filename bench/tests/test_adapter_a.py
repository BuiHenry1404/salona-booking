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
async def test_send_message_includes_message_id(fake_server):
    received: list[dict] = []

    async def on_send_message(sio, sid, data):
        received.append(data)
        await sio.emit("complete", {"answer": "ok"}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        await adapter.send(Turn(say="xin chào"), index=0, timeout_s=2.0)
    finally:
        await adapter.close()

    assert received[0]["message"] == "xin chào"
    assert "message_id" in received[0]
    assert received[0]["message_id"]


@pytest.mark.asyncio
async def test_adapter_emits_confirm_proposal_with_last_proposal_id(fake_server):
    received: list[dict] = []

    async def on_send_message(sio, sid, data):
        received.append({"event": "send_message", "data": data})
        await sio.emit(
            "complete",
            {
                "answer": "Dạ mai 3 giờ chiều được không ạ?",
                "proposal": {
                    "id": "prop-123",
                    "kind": "booking",
                    "expires_at": "2026-10-26T00:00:00+07:00",
                    "when_spoken": "mai 3 giờ chiều",
                },
            },
            to=sid,
        )

    server = FakeServer(on_send_message)

    @server.sio.event
    async def confirm_proposal(sid, data):
        received.append({"event": "confirm_proposal", "data": data})
        await server.sio.emit("complete", {"answer": "Dạ đã đặt lịch.", "proposal": None}, to=sid)

    base_url = await server.start()
    try:
        adapter = AdapterA()
        await adapter.open(base_url, _customer())
        try:
            await adapter.send(Turn(say="mai 3 giờ chiều chị làm tóc"), index=0, timeout_s=2.0)
            record = await adapter.send(Turn(button="yes"), index=1, timeout_s=2.0)
        finally:
            await adapter.close()
    finally:
        await server.stop()

    confirm_events = [e for e in received if e["event"] == "confirm_proposal"]
    assert len(confirm_events) == 1
    sent = confirm_events[0]["data"]
    assert sent["proposal_id"] == "prop-123"
    assert sent["decision"] == "yes"
    assert "message_id" in sent and sent["message_id"]
    assert record.customer_text == "[nút Đồng ý]"
    assert record.answer == "Dạ đã đặt lịch."


@pytest.mark.asyncio
async def test_button_turn_without_pending_proposal_is_recorded_not_raised(fake_server):
    received: list[dict] = []

    async def on_send_message(sio, sid, data):
        received.append(data)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(Turn(button="no"), index=0, timeout_s=0.5)
    finally:
        await adapter.close()

    assert received == []
    assert record.customer_text == "[nút Không]"
    assert record.error is not None
    # Tiền tố riêng để `runner`/`score` phân biệt với lỗi lượt thật — xem
    # Task 7 fix (controller ruling): lượt bấm nút không có proposal phải là
    # một lần chạy KHÔNG HỢP LỆ, không phải một lần chạy trượt.
    assert record.error.startswith("unsupported_button")
    assert "proposal" in record.error


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
    """Review Focus #2 fix: lượt 1 timeout nhưng server vẫn phát sự kiện trễ
    (kể cả `complete`) *trong lúc lượt 2 đang mở* — phải chứng minh adapter
    không gán nhầm dữ liệu trễ của lượt 1 vào lượt 2 đang chờ, chứ không chỉ
    là lượt 2 xong trước khi dữ liệu trễ tới (test cũ không phân biệt được).
    Nếu bỏ `_reconnect()` trong nhánh timeout, test này FAIL vì lượt 2 nhận
    nhầm `complete` trễ của lượt 1 trong lúc đang chờ."""
    turn_count = {"n": 0}

    async def on_send_message(sio, sid, data):
        turn_count["n"] += 1
        if turn_count["n"] == 1:

            async def late_reply() -> None:
                # Lượt 1 timeout ở t=0.2s; phát sự kiện trễ ở t=0.5s — ngay
                # giữa lúc lượt 2 (bắt đầu ngay sau t=0.2s, tự hoàn tất ở
                # t≈0.7s) vẫn còn đang mở.
                await asyncio.sleep(0.5)
                try:
                    await sio.emit("tool_started", {"name": "turn1_tool"}, to=sid)
                    await sio.emit("complete", {"answer": "turn1 answer"}, to=sid)
                except Exception:
                    pass  # kết nối cũ đã bị ngắt — đúng như kỳ vọng

            asyncio.create_task(late_reply())
        else:

            async def turn2_reply() -> None:
                await sio.emit("tool_started", {"name": "turn2_tool"}, to=sid)
                await asyncio.sleep(0.5)  # giữ lượt 2 mở trong lúc t=0.5s tới
                await sio.emit("complete", {"answer": "turn2 answer"}, to=sid)

            asyncio.create_task(turn2_reply())

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record1 = await adapter.send(Turn(say="turn1"), index=0, timeout_s=0.2)
        record2 = await adapter.send(Turn(say="turn2"), index=1, timeout_s=2.0)
        # Để late_reply() (nếu còn đang ngủ) kịp cố gửi vào kết nối cũ và
        # thất bại, tránh cảnh báo "task pending" khi test kết thúc.
        await asyncio.sleep(0.2)
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


@pytest.mark.asyncio
async def test_resend_after_complete_replays_with_same_message_id(fake_server):
    """Task 10: `Turn.resend="after_complete"` gửi lại CÙNG message_id sau khi
    lượt đầu đã xong — chỉ kiểm adapter thực hiện đúng cơ chế (hai emit, cùng
    message_id); server thật phát lại payload đã lưu được kiểm ở repo backend."""
    received: list[dict] = []

    async def on_send_message(sio, sid, data):
        received.append(data)
        await sio.emit("complete", {"answer": "ok", "proposal": None}, to=sid)

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        record = await adapter.send(
            Turn(say="mai 3 giờ chiều", resend="after_complete"), index=0, timeout_s=2.0
        )
    finally:
        await adapter.close()

    assert len(received) == 2
    assert received[0]["message_id"] == received[1]["message_id"]
    assert record.answer == "ok"
    assert record.error is None


@pytest.mark.asyncio
async def test_resend_while_running_sends_duplicate_before_first_completes(fake_server):
    """`resend="while_running"`: bản gửi lại đi NGAY, không chờ lượt đầu —
    mô phỏng server bỏ qua nó (dedupe "running", không trả sự kiện nào)."""
    received: list[dict] = []
    count = {"n": 0}
    gate = asyncio.Event()

    async def on_send_message(sio, sid, data):
        count["n"] += 1
        received.append(data)
        if count["n"] == 1:
            await gate.wait()
            await sio.emit("complete", {"answer": "ok"}, to=sid)
        # Bản gửi lại (n == 2): server thật không trả gì — fake server mô
        # phỏng đúng bằng cách cũng không trả gì ở đây.

    base_url = await fake_server(on_send_message)
    adapter = AdapterA()
    await adapter.open(base_url, _customer())
    try:
        send_task = asyncio.create_task(
            adapter.send(
                Turn(say="mai 3 giờ chiều", resend="while_running"),
                index=0,
                timeout_s=2.0,
            )
        )
        await asyncio.sleep(0.05)
        assert len(received) == 2
        assert received[0]["message_id"] == received[1]["message_id"]
        gate.set()
        record = await send_task
    finally:
        await adapter.close()

    assert record.answer == "ok"
    assert record.error is None


@pytest.mark.asyncio
async def test_reconnect_failure_after_timeout_is_recorded_not_raised():
    """Review Focus #2 fix, item 2: nếu reconnect-vì-timeout cũng thất bại
    (server sập, token bị từ chối, ...), `send()` phải trả về TurnRecord có
    lỗi thay vì raise — và lượt kế tiếp phải tự thử nối lại trước khi gửi,
    cũng không raise nếu vẫn thất bại."""

    async def on_send_message(sio, sid, data):
        return  # không bao giờ trả lời -> timeout

    server = FakeServer(on_send_message)
    base_url = await server.start()

    async def stop_server_soon() -> None:
        # Tắt server trước khi timeout của lượt 1 (0.2s) kích hoạt, để lần
        # reconnect ngay sau đó chắc chắn thất bại (connection refused).
        await asyncio.sleep(0.05)
        await server.stop()

    adapter = AdapterA()
    try:
        await adapter.open(base_url, _customer())
        asyncio.create_task(stop_server_soon())

        record1 = await adapter.send(Turn(say="xin chào"), index=0, timeout_s=0.2)
        assert record1.error is not None
        assert record1.error.startswith("timeout")
        assert "reconnect failed" in record1.error

        # Lượt kế tiếp: adapter tự thử nối lại trước khi gửi; vẫn thất bại
        # (server vẫn tắt) nhưng không raise.
        record2 = await adapter.send(Turn(say="lại nữa"), index=1, timeout_s=0.2)
        assert record2.error is not None
        assert "reconnect failed" in record2.error
    finally:
        await adapter.close()
