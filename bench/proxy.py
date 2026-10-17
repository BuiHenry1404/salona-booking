"""Proxy chèn lỗi giữa app được đo và Azure OpenAI.

Chuyển nguyên request/response (kể cả SSE streaming) tới `upstream`, trừ khi
`set_fault` đang bật một chế độ lỗi:

- `http500`: `count` request kế tiếp nhận HTTP 500, không chạm upstream.
- `hang`: `count` request kế tiếp bị giữ `hang_seconds` giây rồi nhận 504.
- `tool_loop`: `count` request kế tiếp nhận một assistant message chỉ có tool
  call `find_free_slots` (ngày mai, giờ VN) — đẩy agent vào vòng gọi tool.

Hộp đen: module này không import `app`; chỉ nói HTTP với client/upstream.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiohttp import ClientSession, ClientTimeout, web

VN_TZ = timezone(timedelta(hours=7))

# Header các bên hop-by-hop, không chuyển tiếp nguyên văn.
_HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "connection"}

_FAULT_MODES = {None, "http500", "hang", "tool_loop"}


@dataclass
class _FaultState:
    mode: str | None = None
    remaining: int = 0


def _tool_call_payload() -> tuple[str, str]:
    """Trả về (call_id, arguments JSON) cho tool call find_free_slots."""
    tomorrow = (datetime.now(VN_TZ) + timedelta(days=1)).date().isoformat()
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    arguments = json.dumps({"day": tomorrow})
    return call_id, arguments


def _tool_loop_response_body() -> dict:
    call_id, arguments = _tool_call_payload()
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fault-tool-loop",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "find_free_slots",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _tool_loop_sse_chunks() -> list[dict]:
    call_id, arguments = _tool_call_payload()
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    base = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": "fault-tool-loop"}
    first = {
        **base,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "find_free_slots", "arguments": ""},
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    }
    args_chunk = {
        **base,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": arguments},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }
    final = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return [first, args_chunk, final]


class FaultProxy:
    """Proxy HTTP giữa app và Azure OpenAI, có thể chèn lỗi theo yêu cầu."""

    def __init__(self, upstream: str, port: int, hang_seconds: float = 600.0):
        self.upstream = upstream.rstrip("/")
        self.port = port
        self.hang_seconds = hang_seconds
        self._fault = _FaultState()
        self._app = web.Application()
        self._app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.url: str = ""

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()
        actual_port = self._site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.port = actual_port
        self.url = f"http://127.0.0.1:{actual_port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    def set_fault(self, mode: str | None, count: int = 1) -> None:
        if mode not in _FAULT_MODES:
            raise ValueError(f"Chế độ lỗi không hợp lệ: {mode!r}")
        self._fault = _FaultState(mode=mode, remaining=count if mode is not None else 0)

    def _take_fault(self) -> str | None:
        """Trả chế độ lỗi áp dụng cho request hiện tại, giảm đếm nếu khớp."""
        state = self._fault
        if state.mode is None or state.remaining <= 0:
            return None
        mode = state.mode
        state.remaining -= 1
        if state.remaining <= 0:
            self._fault = _FaultState()
        return mode

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        fault = self._take_fault()
        if fault == "http500":
            return web.json_response({"error": "injected fault: http500"}, status=500)
        if fault == "hang":
            await asyncio.sleep(self.hang_seconds)
            return web.json_response({"error": "injected fault: hang"}, status=504)
        if fault == "tool_loop":
            return await self._respond_tool_loop(request)
        return await self._forward(request)

    async def _respond_tool_loop(self, request: web.Request) -> web.StreamResponse:
        body = await _read_json(request)
        if body.get("stream"):
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for chunk in _tool_loop_sse_chunks():
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response(_tool_loop_response_body())

    async def _forward(self, request: web.Request) -> web.StreamResponse:
        target_url = self.upstream + request.path_qs
        forward_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        body = await request.read()

        timeout = ClientTimeout(total=None)
        async with ClientSession(timeout=timeout) as session:
            async with session.request(
                request.method,
                target_url,
                headers=forward_headers,
                data=body,
            ) as upstream_resp:
                content_type = upstream_resp.headers.get("Content-Type", "")
                response_headers = {
                    k: v
                    for k, v in upstream_resp.headers.items()
                    if k.lower() not in _HOP_BY_HOP
                }
                if "text/event-stream" in content_type:
                    resp = web.StreamResponse(
                        status=upstream_resp.status, headers=response_headers
                    )
                    await resp.prepare(request)
                    async for chunk in upstream_resp.content.iter_any():
                        await resp.write(chunk)
                    await resp.write_eof()
                    return resp

                data = await upstream_resp.read()
                return web.Response(
                    status=upstream_resp.status,
                    headers=response_headers,
                    body=data,
                )


async def _read_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except json.JSONDecodeError:
        return {}
