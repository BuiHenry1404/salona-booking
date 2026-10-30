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
"""
from datetime import date
from typing import Any, List, Literal, Optional

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from app.agents.llm import build_chat_model


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
