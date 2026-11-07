"""Dựng/chạy agent cho hai specialist (Appointment, Shop Info) — dùng chung
cho Task 2.4/2.5 (§8/§9 của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

`build_specialist` dựng một `create_agent` với đúng lưới an toàn của
`booking/middleware.py` (dời từ gói cũ ở Task 6.1 — module này CHỈ tiêu thụ,
không copy, hành vi của chúng). `task_message` dựng human message
DUY NHẤT của một task con: khối bối cảnh đầy đủ, rồi khối task (code viết,
không chữ khách), rồi câu khách dưới nhãn `UNTRUSTED` — thứ tự này để model
không lẫn lộn ranh giới giữa chữ hệ thống và chữ khách. `run_specialist`
chạy agent với trần thời gian, thử lại đúng một lần nếu lỗi tạm thời, rồi
giao `to_task_result` đọc `structured_response` (một `SpecialistOutput` HẸP
THEO ACTION — `app.agents.booking.schemas.specialist_output_model`, Task 2.6)
thành `TaskResult` gõ kiểu — KHÔNG parse chữ của AIMessage cuối nữa
(`response_format=ToolStrategy(output_model, handle_errors=False)` bắt model
trả kết quả qua đúng MỘT tool call đúng schema; schema sai không được
LangChain tự gửi lỗi lại cho model thử lại — thử lại vẫn sai)."""
from __future__ import annotations

import asyncio
from typing import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.agents.booking.context import UNTRUSTED, TurnContext, render_context
from app.agents.booking.schemas import TaskResult, TaskSpec
from app.agents.booking.middleware import (NoParallelToolsMiddleware, ToolFailureMiddleware,
                                           ToolRoundLimitMiddleware, is_transient,
                                           model_retry_middleware)
from app.agents.llm import build_chat_model

# Trần số vòng gọi model mỗi task con (giá trị cố định §12.17: `max_tool_rounds_per_task=4`).
MAX_TOOL_ROUNDS_PER_TASK = 4

# Số lần thử CHẠY LẠI TOÀN BỘ `agent.ainvoke` khi lỗi tạm thời (`max_retries_per_task=1`
# §12.17) — khác tầng với `model_retry_middleware()` (thử lại MỘT lần gọi model
# đơn lẻ bên trong một vòng của chính agent đó).
MAX_RUN_ATTEMPTS = 2

TASK_BLOCK = "Task {task_id}: action={action}\nArgs (JSON): {args}\n"


def build_specialist(
    *,
    prompt: str,
    tools: Sequence[BaseTool],
    tag: str,
    output_model: type[BaseModel],
    extra_middleware: Sequence[AgentMiddleware] = (),
):
    """Dựng agent cho một specialist. `tag` gắn vào model để lọc sự kiện
    stream (specialist không stream — `streaming=False`, token của nó không
    bao giờ ra màn hình khách). `output_model` là `SpecialistOutput` HẸP THEO
    ACTION của task này (`app.agents.booking.schemas.specialist_output_model`)
    — ruling sau probe Azure thật (Task 2.6): union rộng của `data` khiến
    model thật chọn sai kiểu ở `is_open_at`; tên tool vẫn là "SpecialistOutput"
    cho mọi action, chỉ `data` hẹp lại."""
    return create_agent(
        model=build_chat_model(tags=[tag], streaming=False, max_retries=0),
        tools=tools,
        system_prompt=prompt,
        middleware=[
            ToolRoundLimitMiddleware(MAX_TOOL_ROUNDS_PER_TASK),
            ToolFailureMiddleware(),
            NoParallelToolsMiddleware(),
            model_retry_middleware(),
            *extra_middleware,
        ],
        # Kết quả cuối của specialist BẮT BUỘC qua MỘT tool call đúng schema
        # — `handle_errors=False` vì schema sai là `failed`/`schema_invalid`,
        # KHÔNG retry (model chép sai thì thử lại vẫn sai, §(bảng lỗi)).
        response_format=ToolStrategy(output_model, handle_errors=False),
        checkpointer=False,
    )


def task_message(spec: TaskSpec, ctx: TurnContext, question: str) -> HumanMessage:
    """Human message DUY NHẤT gửi cho specialist: bối cảnh đầy đủ, rồi task
    (id/action/args lấy từ `spec`, không bịa), rồi câu khách dưới nhãn
    `UNTRUSTED` — câu khách luôn nằm SAU nhãn, không bao giờ đứng trước."""
    task_block = TASK_BLOCK.format(
        task_id=spec.task_id, action=spec.action, args=spec.args.model_dump_json()
    )
    content = f"{render_context(ctx, 'full')}\n\n{task_block}\n{UNTRUSTED}{question}"
    return HumanMessage(content=content)


def _failed(spec: TaskSpec, error_code: str, *, attempts: int, retryable: bool) -> TaskResult:
    return TaskResult(
        task_id=spec.task_id,
        agent=spec.agent,
        action=spec.action,
        status="failed",
        error_code=error_code,
        retryable=retryable,
        attempts=attempts,
    )


def to_task_result(output: BaseModel | None, spec: TaskSpec, *, attempts: int) -> TaskResult:
    """Đọc `structured_response` của specialist thành `TaskResult`. `output`
    là một thực thể của `SpecialistOutput` HẸP THEO ACTION (cùng 4 trường
    `status`/`data`/`missing`/`error_code`, Task 2.6) — không phải lớp
    `SpecialistOutput` gốc. Không có `output` (model không gọi tool kết quả —
    vd. bị `ToolRoundLimitMiddleware` cắt bằng `FALLBACK_ANSWER`, hoặc model
    trả chữ thường không gọi tool nào) → `failed`/`schema_invalid`, không thử
    lại (lỗi của model, thử lại vẫn sai)."""
    if output is None:
        return _failed(spec, "schema_invalid", attempts=attempts, retryable=False)

    return TaskResult(
        task_id=spec.task_id,
        agent=spec.agent,
        action=spec.action,
        status=output.status,
        data=output.data,
        missing=output.missing,
        error_code=output.error_code,
        attempts=attempts,
    )


async def run_specialist(
    agent, spec: TaskSpec, ctx: TurnContext, question: str, *, timeout: float
) -> tuple[TaskResult, list[BaseMessage]]:
    """Chạy một task con. Quá giờ → `failed`/`tool_timeout` (`retryable=True`,
    Orchestrator có thể thử lại task ở lượt khác). Schema sai của structured
    output (`StructuredOutputError` — `handle_errors=False` nên LangChain ném
    thẳng ra, không tự gửi lỗi lại cho model) → `failed`/`schema_invalid`,
    KHÔNG rơi vào nhánh lỗi tạm thời/`model_error` dưới đây. Lỗi tạm thời
    (`is_transient`) khác → chạy lại TOÀN BỘ `agent.ainvoke` đúng một lần
    (`attempts=2`); lỗi khác, hoặc lỗi của lần thử lại, → `failed`/`model_error`.
    Luôn trả kèm `messages` để caller đối chiếu `ToolMessage` (vd.
    `OneProposalPerTaskMiddleware`)."""
    message = task_message(spec, ctx, question)
    attempts = 1
    while True:
        try:
            result = await asyncio.wait_for(agent.ainvoke({"messages": [message]}), timeout)
        except asyncio.TimeoutError:
            return _failed(spec, "tool_timeout", attempts=attempts, retryable=True), []
        except StructuredOutputError:
            return _failed(spec, "schema_invalid", attempts=attempts, retryable=False), []
        except Exception as exc:
            if attempts < MAX_RUN_ATTEMPTS and is_transient(exc):
                attempts += 1
                continue
            return _failed(spec, "model_error", attempts=attempts, retryable=False), []

        messages = result["messages"]
        structured_response = result.get("structured_response")
        return to_task_result(structured_response, spec, attempts=attempts), messages
