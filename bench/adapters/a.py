"""AdapterA: nói chuyện với server thật qua Socket.IO, giống hệt một khách
hàng thật (hộp đen — không `import app`).

Giao thức (đã xác minh với server thật):
- Kết nối `base_url` với `auth={"token": access_token}` (path mặc định
  `/socket.io`). Server phát `connected {"full_name"}` khi kết nối xong.
- Client gửi `send_message {"message": "<text>"}`.
- Server phát theo thứ tự: `turn_started {}`, 0+ `token {"text"}`,
  `tool_started {"name"}`, `tool_finished {"name","ok"}`, rồi đúng một trong
  `complete {"answer"}` hoặc `error {"message", ...}`. `rate_limited` (nếu
  có) cũng kết thúc lượt như `error`.
- Sự kiện lạ được ghi lại vào `TurnRecord.events`, không làm vỡ adapter.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import socketio

from bench.adapters.base import ChatAdapter, Event, TurnRecord

if TYPE_CHECKING:
    from bench.scenario import Turn
    from bench.seed import Customer

_TURN_ENDING_EVENTS = {"complete", "error", "rate_limited"}

_INTENT_TEXT = {
    "agree": "ừ",
    "decline": "không",
}


class AdapterA(ChatAdapter):
    """Adapter Socket.IO: một instance = một cuộc hội thoại của một khách."""

    name = "a"

    def __init__(self) -> None:
        self._sio = socketio.AsyncClient()
        self._sio.on("*", self._on_catch_all)
        self._record: TurnRecord | None = None
        self._start_perf: float = 0.0
        self._done: asyncio.Event | None = None  # tạo lại mỗi lượt gửi

    async def open(self, base_url: str, customer: "Customer") -> None:
        await self._sio.connect(
            base_url,
            auth={"token": customer.token},
            transports=["websocket", "polling"],
        )

    async def send(self, turn: "Turn", index: int, timeout_s: float) -> TurnRecord:
        if turn.intent is not None:
            text = _INTENT_TEXT[turn.intent]
        else:
            text = turn.say or ""

        record = TurnRecord(index=index, customer_text=text)
        self._record = record
        self._done = asyncio.Event()
        self._start_perf = time.monotonic()

        await self._sio.emit("send_message", {"message": text})

        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            record.error = "timeout"
        finally:
            self._record = None

        return record

    async def close(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_perf) * 1000)

    async def _on_catch_all(self, event: str, *args) -> None:
        data = args[0] if args else {}
        self._record_event(event, data)

    def _record_event(self, event_type: str, data) -> None:
        record = self._record
        if record is None:
            # Sự kiện tới sau khi lượt đã kết thúc (vd: timeout) — bỏ qua,
            # không có lượt nào để gắn vào.
            return

        if data is None:
            data = {}
        t_ms = self._elapsed_ms()
        record.events.append(Event(t_ms=t_ms, type=event_type, data=data))

        if event_type == "token":
            if record.first_token_ms is None:
                record.first_token_ms = t_ms
        elif event_type == "tool_started":
            name = data.get("name")
            if name:
                record.tools.append(name)
        elif event_type == "complete":
            record.answer = data.get("answer", "")
            record.complete_ms = t_ms
            self._finish_turn()
        elif event_type in ("error", "rate_limited"):
            record.error = data.get("message")
            self._finish_turn()

    def _finish_turn(self) -> None:
        if self._done is not None:
            self._done.set()
