from datetime import datetime, timezone

import httpx

from bench import tokens
from bench.config import BenchSettings
from bench.tokens import fetch_tokens

SINCE = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)


async def test_fetch_tokens_none_when_unconfigured():
    assert await fetch_tokens(BenchSettings(), "u1", SINCE) is None
    half = BenchSettings(langfuse_host="http://lf", langfuse_public_key="pk")
    assert await fetch_tokens(half, "u1", SINCE) is None
    empty = BenchSettings(langfuse_host="", langfuse_public_key="", langfuse_secret_key="")
    assert await fetch_tokens(empty, "u1", SINCE) is None


def _settings():
    return BenchSettings(
        langfuse_host="http://lf.local", langfuse_public_key="pk", langfuse_secret_key="sk"
    )


def _patch_transport(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(tokens.httpx, "AsyncClient", factory)


async def test_fetch_tokens_sums_generations(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path == "/api/public/traces":
            seen["traces"] = dict(request.url.params)
            return httpx.Response(
                200, json={"data": [{"id": "t1"}, {"id": "t2"}], "meta": {"totalPages": 1}}
            )
        assert request.url.path == "/api/public/observations"
        tid = request.url.params["traceId"]
        data = {
            "t1": [
                {"type": "GENERATION", "usageDetails": {"input": 100, "output": 20}},
                {"type": "GENERATION", "usage": {"input": 50, "output": 5}},
            ],
            "t2": [{"type": "GENERATION", "usage": {"promptTokens": 7, "completionTokens": 3}}],
        }[tid]
        return httpx.Response(200, json={"data": data, "meta": {"totalPages": 1}})

    _patch_transport(monkeypatch, handler)
    result = await fetch_tokens(_settings(), "user-1", SINCE)
    assert result == {"input": 157, "output": 28, "total": 185, "generations": 3, "traces": 2}
    assert seen["traces"]["sessionId"] == "user-1"
    assert seen["traces"]["fromTimestamp"] == "2026-10-17T01:00:00Z"


async def test_fetch_tokens_none_on_http_error(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(401, json={}))
    assert await fetch_tokens(_settings(), "user-1", SINCE) is None
