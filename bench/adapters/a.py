"""AdapterA: nói chuyện với server thật qua Socket.IO, giống hệt một khách
hàng thật (hộp đen — không `import app`).

Giao thức (đã xác minh với server thật):
- Kết nối `base_url` với `auth={"token": access_token}` (path mặc định
  `/socket.io`). Server phát `connected {"full_name"}` khi kết nối xong.
- Client gửi `send_message {"message": "<text>", "message_id": "<uuid>"}`.
- Server phát theo thứ tự: `turn_started {}`, 0+ `token {"text"}`,
  `tool_started {"name"}`, `tool_finished {"name","ok"}`, rồi đúng một trong
  `complete {"answer", "proposal"}` hoặc `error {"message", ...}`.
  `rate_limited` (nếu có) cũng kết thúc lượt như `error`. `proposal` (khi
  không null) có `id`, `kind`, `expires_at`, `when_spoken` — adapter nhớ
  `proposal.id` mới nhất để dùng cho lượt bấm nút kế tiếp.
- Lượt bấm nút (`Turn.button`): client gửi `confirm_proposal {"proposal_id",
  "decision", "message_id"}` thay vì `send_message`, dùng `proposal.id` của
  `complete` gần nhất. Ref cũ không hỗ trợ nút (không gửi `proposal`) → nếu
  chưa từng thấy `proposal.id` nào, lượt bấm nút được ghi lỗi tiền tố
  `unsupported_button:` thay vì gửi đi — adapter không crash và `bench run`
  vẫn tiếp tục so sánh các ref/kịch bản khác, nhưng runner dừng NGAY kịch
  bản này tại lượt đó và `score.finalize_score` báo lần chạy KHÔNG HỢP LỆ
  (`invalid_reason="unsupported_button"`, loại khỏi mẫu số pass_rate) thay
  vì tính là trượt, để không lệch tỉ lệ đạt khi so ref cũ/mới.
- Sự kiện lạ được ghi lại vào `TurnRecord.events`, không làm vỡ adapter.
"""

from __future__ import annotations

import asyncio
import time
import uuid
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

_BUTTON_TEXT = {
    "yes": "[nút Đồng ý]",
    "no": "[nút Không]",
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
        self._base_url: str = ""
        self._token: str = ""
        # True sau khi một lần reconnect-vì-timeout thất bại (server sập,
        # token bị từ chối, ...) — lượt kế tiếp phải thử nối lại trước khi
        # gửi, thay vì gửi trên kết nối hỏng.
        self._needs_reconnect: bool = False
        # `proposal.id` của `complete` gần nhất — dùng cho lượt bấm nút kế
        # tiếp. None nếu chưa có `complete` nào, hoặc proposal gần nhất là
        # null (đã bị huỷ/hết hạn, hoặc ref không gửi proposal).
        self._last_proposal_id: str | None = None

    async def open(self, base_url: str, customer: "Customer") -> None:
        self._base_url = base_url
        self._token = customer.token
        await self._connect()

    async def _connect(self) -> None:
        await self._sio.connect(
            self._base_url,
            auth={"token": self._token},
            transports=["websocket", "polling"],
        )

    async def send(self, turn: "Turn", index: int, timeout_s: float) -> TurnRecord:
        if turn.button is not None:
            text = _BUTTON_TEXT[turn.button]
        elif turn.intent is not None:
            text = _INTENT_TEXT[turn.intent]
        else:
            text = turn.say or ""

        record = TurnRecord(index=index, customer_text=text)

        if turn.button is not None and self._last_proposal_id is None:
            # Chưa từng thấy proposal nào (ref cũ không gửi proposal, hoặc
            # proposal gần nhất đã bị tiêu thụ/huỷ) — không có gì để bấm nút
            # vào. Ghi lỗi rõ ràng, không emit, không raise, để kịch bản vẫn
            # chạy tiếp/so sánh được với ref cũ.
            record.error = (
                "unsupported_button: button turn without a pending proposal "
                "(ref does not support buttons?)"
            )
            return record

        if self._needs_reconnect:
            # Lượt trước timeout và lần reconnect ngay sau đó đã thất bại —
            # thử lại trước khi gửi, để không gửi trên một kết nối đã hỏng.
            try:
                await self._reconnect()
                self._needs_reconnect = False
            except Exception as exc:  # noqa: BLE001 — ghi lại, không raise
                record.error = f"timeout; reconnect failed: {exc}"
                return record

        self._record = record
        self._done = asyncio.Event()
        self._start_perf = time.monotonic()

        if turn.button is not None:
            await self._sio.emit(
                "confirm_proposal",
                {
                    "proposal_id": self._last_proposal_id,
                    "decision": turn.button,
                    "message_id": str(uuid.uuid4()),
                },
            )
        else:
            await self._sio.emit(
                "send_message",
                {"message": text, "message_id": str(uuid.uuid4())},
            )

        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            record.error = "timeout"
            # Giao thức không có turn id: một sự kiện tới trễ của lượt này
            # (vd: complete tới sau khi đã timeout) có thể lẫn vào lượt kế
            # tiếp. Ngắt và nối lại kết nối Socket.IO (cùng base_url/token)
            # để các sự kiện trễ đó chết theo kết nối cũ trước khi trả về.
            self._record = None
            try:
                await self._reconnect()
            except Exception as exc:  # noqa: BLE001 — timeout không bao giờ raise
                record.error = f"timeout; reconnect failed: {exc}"
                self._needs_reconnect = True
        finally:
            self._record = None

        return record

    async def _reconnect(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()
        await self._connect()

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
            proposal = data.get("proposal")
            self._last_proposal_id = proposal.get("id") if proposal else None
            self._finish_turn()
        elif event_type in ("error", "rate_limited"):
            record.error = data.get("message")
            self._finish_turn()

    def _finish_turn(self) -> None:
        if self._done is not None:
            self._done.set()
