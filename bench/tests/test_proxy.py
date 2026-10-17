"""Test cho FaultProxy — proxy chèn lỗi giữa app và Azure OpenAI.

Dựng một "upstream" giả bằng aiohttp (không gọi Azure thật) trả JSON/SSE cố
định, rồi trỏ proxy vào đó để kiểm forward nguyên trạng và các chế độ lỗi.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
from aiohttp import web

from bench.proxy import FaultProxy

VN_TZ = timezone(timedelta(hours=7))

FAKE_CHAT_RESPONSE = {
    "id": "chatcmpl-fake",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "xin chào"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

SSE_CHUNKS = [
    {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "xin"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": " chào"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
]


async def _fake_upstream_handler(request: web.Request) -> web.StreamResponse:
    body = await request.json() if request.can_read_body else {}
    _fake_upstream_handler.last_request = {  # type: ignore[attr-defined]
        "path": request.path,
        "query": dict(request.query),
        "headers": dict(request.headers),
        "body": body,
    }
    if body.get("stream"):
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)
        for chunk in SSE_CHUNKS:
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response(FAKE_CHAT_RESPONSE)


async def _gzip_json_handler(request: web.Request) -> web.Response:
    """Trả JSON cố định nén gzip với header Content-Encoding: gzip.

    Giả lập Azure trả response nén — aiohttp ClientSession (ở proxy) tự giải
    nén body này trước khi đọc, nên proxy KHÔNG được giữ nguyên header
    Content-Encoding khi chuyển tiếp (xem bench/proxy.py _RESPONSE_STRIP_HEADERS).
    """
    payload = gzip.compress(json.dumps(FAKE_CHAT_RESPONSE).encode())
    return web.Response(
        status=200,
        body=payload,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )


async def _gzip_sse_handler(request: web.Request) -> web.Response:
    """SSE tương tự nhưng toàn bộ payload (nhiều dòng data:) được nén gzip."""
    raw = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in SSE_CHUNKS) + b"data: [DONE]\n\n"
    payload = gzip.compress(raw)
    return web.Response(
        status=200,
        body=payload,
        headers={"Content-Type": "text/event-stream", "Content-Encoding": "gzip"},
    )


@pytest.fixture
async def fake_upstream():
    app = web.Application()
    app.router.add_route("GET", "/gzip-json", _gzip_json_handler)
    app.router.add_route("GET", "/gzip-sse", _gzip_sse_handler)
    app.router.add_route("*", "/{tail:.*}", _fake_upstream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.fixture
async def proxy(fake_upstream):
    p = FaultProxy(upstream=fake_upstream, port=0)
    await p.start()
    yield p
    await p.stop()


def _request_path(deployment: str = "gpt-4o") -> str:
    return f"/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview"


async def test_passthrough_json_and_sse(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data == FAKE_CHAT_RESPONSE

        async with session.post(url, json={"stream": True, "messages": []}, headers=headers) as resp:
            assert resp.status == 200
            assert "text/event-stream" in resp.headers["Content-Type"]
            raw = await resp.read()
            text = raw.decode()
            assert text.startswith("data: ")
            assert text.strip().endswith("data: [DONE]")
            lines = [l for l in text.split("\n\n") if l.strip()]
            assert len(lines) == len(SSE_CHUNKS) + 1


async def test_http500_then_recovers(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    proxy.set_fault("http500")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 500

        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data == FAKE_CHAT_RESPONSE


async def test_http500_count(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    proxy.set_fault("http500", count=2)

    async with aiohttp.ClientSession() as session:
        for _ in range(2):
            async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
                assert resp.status == 500
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 200


async def test_tool_loop_returns_tool_call_non_stream(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    proxy.set_fault("tool_loop")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 200
            data = await resp.json()

    choice = data["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["role"] == "assistant"
    assert len(message["tool_calls"]) == 1
    call = message["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "find_free_slots"
    args = json.loads(call["function"]["arguments"])
    tomorrow = (datetime.now(VN_TZ) + timedelta(days=1)).date().isoformat()
    assert args["day"] == tomorrow
    assert "usage" in data

    # Only one fault request was configured; the next one passes through.
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            data2 = await resp.json()
    assert data2 == FAKE_CHAT_RESPONSE


async def test_tool_loop_returns_tool_call_stream(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    proxy.set_fault("tool_loop")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": True, "messages": []}, headers=headers) as resp:
            assert resp.status == 200
            raw = await resp.read()

    text = raw.decode()
    assert text.strip().endswith("data: [DONE]")
    events = [json.loads(l[len("data: "):]) for l in text.split("\n\n") if l.strip() and l.strip() != "data: [DONE]"]
    assert len(events) >= 1

    found_name = False
    found_finish = False
    for event in events:
        delta = event["choices"][0]["delta"]
        if delta.get("tool_calls"):
            tc = delta["tool_calls"][0]
            if tc.get("type") is not None:
                assert tc["type"] == "function"
            if tc["function"].get("name") == "find_free_slots":
                found_name = True
        if event["choices"][0].get("finish_reason") == "tool_calls":
            found_finish = True
    assert found_name
    assert found_finish


async def test_set_fault_none_clears_fault(proxy):
    url = proxy.url + _request_path()
    headers = {"api-key": "secret", "content-type": "application/json"}

    proxy.set_fault("http500")
    proxy.set_fault(None)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
            assert resp.status == 200


async def test_forwards_headers_and_query(proxy, fake_upstream):
    url = proxy.url + _request_path("my-deployment")
    headers = {"api-key": "secret-key", "content-type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"stream": False, "messages": [{"role": "user", "content": "hi"}]}, headers=headers) as resp:
            assert resp.status == 200

    seen = _fake_upstream_handler.last_request  # type: ignore[attr-defined]
    assert seen["path"] == "/openai/deployments/my-deployment/chat/completions"
    assert seen["query"] == {"api-version": "2024-02-15-preview"}
    assert seen["headers"]["api-key"] == "secret-key"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


async def test_passthrough_decodes_gzip_json(proxy):
    url = proxy.url + "/gzip-json"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Encoding") is None
            data = await resp.json()
            assert data == FAKE_CHAT_RESPONSE


async def test_passthrough_decodes_gzip_sse(proxy):
    url = proxy.url + "/gzip-sse"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Encoding") is None
            raw = await resp.read()

    text = raw.decode()
    assert text.strip().endswith("data: [DONE]")
    lines = [l for l in text.split("\n\n") if l.strip() and l.strip() != "data: [DONE]"]
    events = [json.loads(l[len("data: "):]) for l in lines]
    assert events == SSE_CHUNKS


async def test_hang_returns_504_after_duration():
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _fake_upstream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    upstream = f"http://127.0.0.1:{port}"

    p = FaultProxy(upstream=upstream, port=0, hang_seconds=0.2)
    await p.start()
    try:
        p.set_fault("hang")
        url = p.url + _request_path()
        headers = {"api-key": "secret", "content-type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"stream": False, "messages": []}, headers=headers) as resp:
                assert resp.status == 504
    finally:
        await p.stop()
        await runner.cleanup()
