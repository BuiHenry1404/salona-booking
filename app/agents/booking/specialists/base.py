"""Dựng/chạy agent cho hai specialist (Appointment, Shop Info) — dùng chung
cho Task 2.4/2.5 (§8/§9 của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

`build_specialist` dựng một `create_agent` với đúng lưới an toàn M6 sẽ dời
sang `booking/middleware.py` (đang import từ `booking_graph` — Task này CHỈ
tiêu thụ, không copy, hành vi của chúng). `task_message` dựng human message
DUY NHẤT của một task con: khối bối cảnh đầy đủ, rồi khối task (code viết,
không chữ khách), rồi câu khách dưới nhãn `UNTRUSTED` — thứ tự này để model
không lẫn lộn ranh giới giữa chữ hệ thống và chữ khách. `run_specialist`
chạy agent với trần thời gian, thử lại đúng một lần nếu lỗi tạm thời, rồi
giao `parse_task_result` đọc JSON cuối cùng thành `TaskResult` gõ kiểu.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.agents.booking.context import UNTRUSTED, TurnContext, render_context
from app.agents.booking.schemas import SpecialistOutput, TaskResult, TaskSpec
from app.agents.booking_graph.agents import is_transient, model_retry_middleware
from app.agents.booking_graph.middleware.tools import (NoParallelToolsMiddleware,
                                                        ToolFailureMiddleware,
                                                        ToolRoundLimitMiddleware)
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
    extra_middleware: Sequence[AgentMiddleware] = (),
):
    """Dựng agent cho một specialist. `tag` gắn vào model để lọc sự kiện
    stream (specialist không stream — `streaming=False`, token của nó không
    bao giờ ra màn hình khách)."""
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


def parse_task_result(text: str, spec: TaskSpec, *, attempts: int) -> TaskResult:
    """Đọc câu trả lời cuối của specialist thành `TaskResult`. Không parse
    được (không phải JSON, hoặc JSON sai `SpecialistOutput`) → `failed` /
    `schema_invalid`, không thử lại (lỗi của model, thử lại vẫn sai)."""
    try:
        parsed = SpecialistOutput.model_validate_json(text)
    except (ValidationError, ValueError, TypeError):
        return _failed(spec, "schema_invalid", attempts=attempts, retryable=False)

    return TaskResult(
        task_id=spec.task_id,
        agent=spec.agent,
        action=spec.action,
        status=parsed.status,
        data=parsed.data,
        missing=parsed.missing,
        error_code=parsed.error_code,
        attempts=attempts,
    )


def _last_text(messages: list[BaseMessage]) -> str:
    if not messages:
        return ""
    content = messages[-1].content
    return content if isinstance(content, str) else ""


async def run_specialist(
    agent, spec: TaskSpec, ctx: TurnContext, question: str, *, timeout: float
) -> tuple[TaskResult, list[BaseMessage]]:
    """Chạy một task con. Quá giờ → `failed`/`tool_timeout` (`retryable=True`,
    Orchestrator có thể thử lại task ở lượt khác). Lỗi tạm thời (`is_transient`)
    → chạy lại TOÀN BỘ `agent.ainvoke` đúng một lần (`attempts=2`); lỗi khác,
    hoặc lỗi của lần thử lại, → `failed`/`model_error`. Luôn trả kèm
    `messages` để caller đối chiếu `ToolMessage` (vd. `OneProposalPerTaskMiddleware`)."""
    message = task_message(spec, ctx, question)
    attempts = 1
    while True:
        try:
            result = await asyncio.wait_for(agent.ainvoke({"messages": [message]}), timeout)
        except asyncio.TimeoutError:
            return _failed(spec, "tool_timeout", attempts=attempts, retryable=True), []
        except Exception as exc:
            if attempts < MAX_RUN_ATTEMPTS and is_transient(exc):
                attempts += 1
                continue
            return _failed(spec, "model_error", attempts=attempts, retryable=False), []

        messages = result["messages"]
        return parse_task_result(_last_text(messages), spec, attempts=attempts), messages
