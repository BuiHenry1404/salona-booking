"""Task 0.6: kiểm `strict=True` cho tool-calling có hình dạng giống §5.1
(Literal enum, `X | None`, model lồng, list, `date`) trước khi Orchestrator
(M3) dựa vào nó cho 5 tool thật.

`test_probe_schema_is_strict_safe` không tốn tiền, không marker — chạy trong
suite mặc định: duyệt JSON Schema do `convert_to_openai_tool(..., strict=True)`
sinh ra ở MỌI độ sâu, khẳng định không còn khoá `default`/`minimum`/`maximum`
(nếu còn, Azure strict mode từ chối với 400).

`test_strict_tool_schema_is_accepted_by_the_deployment` gọi Azure thật — có
marker `llm`, bị loại khỏi lần chạy mặc định bởi addopts
(`-m "not llm"` trong pyproject.toml). Chỉ chạy khi chủ dự án đã cho phép,
bằng `pytest tests/test_strict_schema_llm.py -m llm`.

Task 3.5: `test_real_orchestrator_tools_are_accepted_strict` lặp lại kiểm
tra đó với 5 tool THẬT của Orchestrator (`make_orchestrator_tools`, §5.1)
thay vì tool probe giả — cũng `llm`, cũng chỉ chạy một lần khi có phép.
"""
from datetime import date
from typing import Any, List, Literal, Optional

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from app.agents.booking.context import AppointmentRef
from app.agents.booking.orchestrator.tools import OrchestratorDeps, make_orchestrator_tools
from app.agents.llm import build_chat_model
from app.core.clock import now_utc
from tests.factories import make_ctx


class ProbeHint(BaseModel):
    """Mô phỏng một tham số lồng kiểu §5.1: mọi field đều `X | None`, không
    field nào có default — strict mode của OpenAI/Azure yêu cầu TẤT CẢ field
    phải nằm trong `required`, kể cả field "tuỳ chọn" (biểu diễn bằng
    `| None` chứ không phải bằng default/absence)."""

    day: Optional[date]
    hour: Optional[int]
    part: Optional[Literal["sang", "chieu"]]
    items: Optional[List[str]]


def strict_probe_tools() -> List[BaseTool]:
    """Một tool `probe(action, hint)` dùng để dò hình dạng schema, không phải
    tool nghiệp vụ thật — 5 tool thật thay vào Task 3.6 (M3)."""

    @tool
    def probe(action: Literal["a", "b"], hint: ProbeHint) -> str:
        """Probe tool for strict-schema testing. Not a real business tool."""
        return "{}"

    return [probe]


def _walk(node: Any, keys: set) -> set:
    """Trả về tập các khoá cấm (`default`/`minimum`/`maximum`) tìm thấy ở bất
    kỳ độ sâu nào trong `node` (dict/list lồng nhau)."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                found.add(k)
            found |= _walk(v, keys)
    elif isinstance(node, list):
        for item in node:
            found |= _walk(item, keys)
    return found


def test_probe_schema_is_strict_safe():
    """`convert_to_openai_tool(..., strict=True)` không thêm `additionalProperties`
    hay `required` một cách tự giác xoá `default`/`minimum`/`maximum` khỏi
    schema do Pydantic sinh ra — Azure strict mode từ chối các khoá đó."""
    [probe] = strict_probe_tools()
    schema = convert_to_openai_tool(probe, strict=True)

    forbidden = _walk(schema, {"default", "minimum", "maximum"})

    assert forbidden == set(), (
        f"Schema vẫn còn khoá strict-mode từ chối: {forbidden}\n{schema}"
    )


class _FakeLedger:
    """Ghi lại `append_call` — không chạm Mongo (test này không gọi tool thật,
    chỉ cần `make_orchestrator_tools` dựng được 5 tool để bind strict)."""

    def __init__(self) -> None:
        self.appended: list[tuple[str, object]] = []

    async def append_call(self, turn_id: str, call) -> None:
        self.appended.append((turn_id, call))


def _noop_run(*_a, **_kw):
    raise AssertionError("run_appointment/run_shop không nên được gọi trong test này")


def _real_orchestrator_deps() -> OrchestratorDeps:
    """`ctx` có đúng 1 lịch hẹn sắp tới (§12.17: enum `appointment_id` của
    `appointment_task.args` phải khớp `ctx.upcoming`) — mô phỏng lượt thật
    mà Orchestrator (M3) sẽ gửi cho deployment."""
    ctx = make_ctx(upcoming=[AppointmentRef(id="a1", start_at=now_utc(), spoken="mai 9h")])
    return OrchestratorDeps(
        db=None,
        user=None,
        ctx=ctx,
        ledger=_FakeLedger(),
        turn_id="turn-1",
        question="chào em",
        max_rounds=8,
        run_appointment=_noop_run,
        run_shop=_noop_run,
    )


@pytest.mark.llm
@pytest.mark.asyncio
async def test_real_orchestrator_tools_are_accepted_strict():
    """Gọi Azure thật đúng MỘT lần (có phép chủ dự án — Task 3.5 Step 2) để
    xác nhận deployment hiện tại không trả 400 cho 5 tool THẬT của
    Orchestrator khi `strict=True` (thay vì tool probe giả của Task 0.6)."""
    deps = _real_orchestrator_deps()
    tools = make_orchestrator_tools(deps)
    model = build_chat_model(tags=["orchestrate"], streaming=False, max_retries=0).bind_tools(
        tools, strict=True, tool_choice="required"
    )
    reply = await model.ainvoke(
        [HumanMessage("Everything needed has been gathered; end the turn now by calling finish.")]
    )
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "finish"


@pytest.mark.llm
@pytest.mark.asyncio
async def test_strict_tool_schema_is_accepted_by_the_deployment():
    """Gọi Azure thật đúng MỘT lần (có phép chủ dự án — Task 0.6 Step 3) để
    xác nhận deployment hiện tại không trả 400 cho schema hình dạng §5.1 khi
    `strict=True`."""
    model = build_chat_model(tags=["probe"], streaming=False, max_retries=0).bind_tools(
        strict_probe_tools(), strict=True, tool_choice="required"
    )
    reply = await model.ainvoke(
        [HumanMessage("Call the tool with action a for tomorrow morning at 9.")]
    )
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "probe"
