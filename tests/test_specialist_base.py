"""Test cho `app/agents/booking/specialists/base.py` (Task 2.3; Task 2.6
chuyển kết quả cuối sang `response_format=ToolStrategy(<output_model hẹp theo
action>, ...)`) — dựng agent, chạy task, đọc `TaskResult`. Dùng
`ScriptedChatModel` (không gọi Azure thật)."""
import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agents.booking.context import UNTRUSTED, render_context
from app.agents.booking.schemas import ShopHoursOut, specialist_output_model
from app.agents.booking.specialists.base import build_specialist, run_specialist, task_message
from app.agents.booking_graph.middleware.tools import FALLBACK_ANSWER
from tests.factories import make_ctx, make_spec
from tests.fakes import Hang, final_output, scripted, tool_call


def _patch_build_chat_model(monkeypatch, model):
    monkeypatch.setattr(
        "app.agents.booking.specialists.base.build_chat_model", lambda **kwargs: model
    )


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))


class _FlakyAgent:
    """Agent giả cho test retry của `run_specialist` — không dính dáng tới
    `ModelRetryMiddleware` (middleware đó thử lại MỘT LẦN GỌI MODEL bên trong
    một task; test này kiểm tra `run_specialist` thử lại CẢ TASK)."""

    def __init__(self, exceptions):
        self._exceptions = list(exceptions)
        self.calls = 0

    async def ainvoke(self, _input):
        self.calls += 1
        raise self._exceptions.pop(0)


def test_task_message_has_context_then_task_then_untrusted_question():
    ctx = make_ctx()
    spec = make_spec(agent="appointment", action="resolve_time", task_id="t1.0")
    question = "mai 3h chiều nhé"

    message = task_message(spec, ctx, question)

    assert message.content.startswith(render_context(ctx, "full"))
    task_idx = message.content.index("Task t1.0")
    untrusted_idx = message.content.index(UNTRUSTED)
    question_idx = message.content.index(question)
    assert task_idx < untrusted_idx < question_idx
    # Câu khách nằm NGAY SAU nhãn, không có chữ nào chen vào giữa.
    assert message.content[untrusted_idx + len(UNTRUSTED):].startswith(question)


async def test_structured_output_becomes_ok_result_with_typed_data(monkeypatch):
    output = final_output(
        status="ok",
        data={
            "open": {"iso": "2026-11-05T08:00:00+07:00", "spoken": "8 giờ sáng"},
            "close": {"iso": "2026-11-05T19:00:00+07:00", "spoken": "7 giờ tối"},
            "closed_days": [],
        },
        missing=[],
        error_code=None,
    )
    _patch_build_chat_model(monkeypatch, scripted([output], tags=["shop_info"]))
    agent = build_specialist(
        prompt="test", tools=[], tag="shop_info", output_model=specialist_output_model("get_hours")
    )
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.1")
    ctx = make_ctx()

    result, _messages = await run_specialist(agent, spec, ctx, "may gio mo cua", timeout=5)

    assert result.status == "ok"
    assert isinstance(result.data, ShopHoursOut)
    assert result.task_id == "t1.1"
    assert result.attempts == 1


async def test_invalid_structured_output_args_is_schema_invalid_not_retried(monkeypatch):
    bad = tool_call("SpecialistOutput", {"status": "not_a_real_status"})
    model = scripted([bad], tags=["shop_info"])
    _patch_build_chat_model(monkeypatch, model)
    agent = build_specialist(
        prompt="test", tools=[], tag="shop_info", output_model=specialist_output_model("get_hours")
    )
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.1")
    ctx = make_ctx()

    result, _messages = await run_specialist(agent, spec, ctx, "may gio mo cua", timeout=5)

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"
    assert result.retryable is False
    # handle_errors=False: schema sai không được LangChain tự gửi lỗi lại
    # cho model thử lại trong cùng task.
    assert len(model.calls) == 1


async def test_plain_text_without_tool_call_is_schema_invalid(monkeypatch):
    model = scripted([AIMessage(content="khong lam duoc")], tags=["shop_info"])
    _patch_build_chat_model(monkeypatch, model)
    agent = build_specialist(
        prompt="test", tools=[], tag="shop_info", output_model=specialist_output_model("get_hours")
    )
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.1")
    ctx = make_ctx()

    result, _messages = await run_specialist(agent, spec, ctx, "may gio mo cua", timeout=5)

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"


async def test_run_specialist_times_out_as_tool_timeout(monkeypatch):
    _patch_build_chat_model(monkeypatch, scripted([Hang(100)], tags=["appointment"]))
    agent = build_specialist(
        prompt="test", tools=[], tag="appointment", output_model=specialist_output_model("resolve_time")
    )
    spec = make_spec(task_id="t1.0")
    ctx = make_ctx()

    result, messages = await run_specialist(agent, spec, ctx, "mai 3h", timeout=0.1)

    assert result.status == "failed"
    assert result.error_code == "tool_timeout"
    assert result.retryable is True
    assert messages == []


async def test_transient_error_is_retried_once_then_reported():
    agent = _FlakyAgent([_connection_error(), _connection_error()])
    spec = make_spec(task_id="t1.0")
    ctx = make_ctx()

    result, messages = await run_specialist(agent, spec, ctx, "mai 3h", timeout=5)

    assert result.status == "failed"
    assert result.error_code == "model_error"
    assert result.attempts == 2
    assert agent.calls == 2
    assert messages == []


async def test_tool_round_limit_is_four_per_task(monkeypatch):
    @tool
    def noop(x: str) -> str:
        """Tool giả, luôn trả về chuỗi cố định."""
        return "ok"

    replies = [tool_call("noop", {"x": "a"}) for _ in range(5)]
    model = scripted(replies, tags=["appointment"])
    _patch_build_chat_model(monkeypatch, model)
    agent = build_specialist(
        prompt="test", tools=[noop], tag="appointment",
        output_model=specialist_output_model("resolve_time"),
    )
    spec = make_spec(task_id="t1.0")
    ctx = make_ctx()

    result, messages = await run_specialist(agent, spec, ctx, "mai 3h", timeout=5)

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"
    assert len(model.calls) == 4
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == FALLBACK_ANSWER
