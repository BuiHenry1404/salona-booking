"""Giới hạn LLM (A-04): timeout mỗi lần gọi, retry có giới hạn.

Task 6.3 (M6-cutover): các test lượt đầu-cuối (trần giờ lượt, retry/degrade
qua `run_turn`) đã có bản tương đương trên gói mới ở `tests/test_run_turn.py`
(`test_turn_timeout_emits_the_same_error`, `test_turn_time_budget_inequality`)
và `tests/test_conversation_agent.py` (`test_model_error_returns_the_fallback_as_code`,
`test_timeout_returns_the_fallback`) — không port lại ở đây. Giữ lại: phần
`is_transient`/retry thuần (`app.agents.booking.middleware`) và cấu hình
`llm.build_chat_model`/`model_kwargs` chưa có test trực tiếp nào khác.
"""
import asyncio

import pytest
from langchain.agents.middleware import ModelRetryMiddleware

from app.agents import llm
from app.agents.booking import middleware
from app.core.config import settings


@pytest.fixture
def azure(monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(settings, "llm_provider", "azure")
    monkeypatch.setattr(settings, "azure_openai_api_key", SecretStr("khoa-gia"))
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://gia.openai.azure.com")


def test_chat_model_has_a_timeout_and_no_sdk_retries(azure):
    model = llm.build_chat_model(tags=["respond"], max_retries=0)
    assert model.request_timeout == llm.LLM_TIMEOUT_SECONDS == 20
    assert model.max_retries == 0


def test_reasoning_effort_drops_temperature():
    """Azure trả 400 nếu gửi `temperature` khác mặc định cùng `reasoning_effort`."""
    kw = llm.model_kwargs(temperature=0.0, streaming=False, timeout=8, max_retries=1,
                          reasoning_effort="low")
    assert kw["reasoning_effort"] == "low" and "temperature" not in kw


def test_without_reasoning_temperature_is_kept():
    kw = llm.model_kwargs(temperature=0.2, streaming=True, timeout=20, max_retries=0,
                          reasoning_effort=None)
    assert kw["temperature"] == 0.2 and "reasoning_effort" not in kw


def test_other_callers_keep_the_sdk_retries(azure):
    """F11: chỉ model agent (có `ModelRetryMiddleware` bọc) tắt retry SDK;
    orchestrator/rewrite/phrase/timeparse giữ mặc định của SDK như A."""
    model = llm.build_chat_model(tags=["supervisor"])
    assert model.request_timeout == llm.LLM_TIMEOUT_SECONDS
    assert model.max_retries is None
    assert model.root_async_client.max_retries == 2          # mặc định openai SDK


def test_agent_retries_the_model_once_and_then_raises():
    mw = middleware.model_retry_middleware()
    assert isinstance(mw, ModelRetryMiddleware)
    assert mw.max_retries == 1 and mw.on_failure == "error" and mw.initial_delay == 0.5


def api_error(status, message="azure"):
    """Lỗi openai SDK thật (cần httpx.Response) — retry chỉ nhận lỗi tạm thời."""
    import httpx
    import openai
    request = httpx.Request("POST", "https://gia.openai.azure.com/chat")
    if status == "timeout":
        return openai.APITimeoutError(request=request)
    if status == "connection":
        return openai.APIConnectionError(request=request)
    response = httpx.Response(status, request=request)
    classes = {400: openai.BadRequestError, 401: openai.AuthenticationError,
               403: openai.PermissionDeniedError, 404: openai.NotFoundError,
               429: openai.RateLimitError, 500: openai.InternalServerError}
    cls = classes.get(status, openai.APIStatusError)
    return cls(message, response=response, body=None)


@pytest.mark.parametrize("status", ["timeout", "connection", 429, 500, 502, 503])
def test_transient_errors_are_retried(status):
    assert middleware.is_transient(api_error(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_deterministic_errors_are_not_retried(status):
    assert not middleware.is_transient(api_error(status))


def test_content_filter_and_unknown_errors_are_not_retried():
    assert not middleware.is_transient(api_error(400, "content_filter: ResponsibleAIPolicyViolation"))
    assert not middleware.is_transient(RuntimeError("lỗi lập trình"))
    assert not middleware.is_transient(ValueError("context length"))


def test_plain_timeouts_are_transient():
    import httpx
    assert middleware.is_transient(asyncio.TimeoutError())
    assert middleware.is_transient(httpx.ReadTimeout("chậm"))
    assert middleware.is_transient(httpx.ConnectError("mất mạng"))


def test_retry_middleware_uses_the_transient_filter():
    assert middleware.model_retry_middleware().retry_on is middleware.is_transient
