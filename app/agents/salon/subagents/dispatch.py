"""Hàm điều phối DÙNG CHUNG cho mọi agent con (spec §3.1, §4.1, §5, §5.1;
docs/architech-agent.md luật 1, 6, 7).

Ba tool giao việc của Orchestrator (`appointment_task`, `shop_info_task`,
`service_task` — Task 11) chỉ khai schema rồi gọi `run_subagent`; mọi bước
còn lại nằm ở đây, viết MỘT lần:

1. đọc phiên chờ của agent từ bối cảnh lượt (`SnapshotHolder`, chỉ phiên còn hạn);
2. dựng agent con MỖI LẦN GỌI (`create_agent`, `checkpointer=False` — không
   trạng thái, không `interrupt()`; enum id trong schema tool là của khách
   này, lượt này);
3. gửi MỘT HumanMessage: khối bối cảnh theo audience + bộ nhớ phiên (nếu làm
   tiếp) + lời dặn + câu khách dưới nhãn untrusted;
4. chạy với trần thời gian; lỗi → `failed` với mã `timeout | model_error |
   schema_invalid`;
5. `spec.extract` trích dữ kiện từ ToolMessage (KHÔNG từ chữ model);
6. ghi phiên qua `WaitingSessionService` — phiên KHÔNG bao giờ vào state graph;
7. trả `Command(update=...)` gồm ToolMessage tóm tắt + `turn_facts`.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Awaitable, Callable, Literal, Optional, Sequence

import httpx
import openai
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
)
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.exceptions import ModelError
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field, model_validator

from app.agents.llm import build_chat_model
from app.agents.salon.orchestrator.state import SalonContext, SalonState
from app.agents.salon.shared.context import (
    UNTRUSTED_LABEL,
    SnapshotHolder,
    TurnSnapshot,
    render_context,
)
from app.agents.salon.shared.schemas import (
    AgentName,
    AgentStatusFact,
    Moment,
    QuestionField,
    TurnFact,
)
from app.core.clock import now_utc
from app.core.config import settings
from app.core.logging import get_logger
from app.models.user import User
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.services.waiting_session import WaitingSessionService

logger = get_logger(__name__)

# Thử lại MỘT lần gọi model khi lỗi tạm thời (khác tầng: không chạy lại cả agent).
MODEL_RETRIES = 1
MODEL_RETRY_DELAY_SECONDS = 0.5

ErrorCode = Literal["timeout", "model_error", "schema_invalid"]


class SubagentResult(BaseModel):
    """Kết quả cuối của MỌI agent con, qua `ToolStrategy` (một tool call đúng
    schema). Hẹp có chủ ý: không có `data` tự do — id/iso/giờ do code trích từ
    ToolMessage (`Extracted`), không lấy từ chữ model."""

    status: Literal["ok", "need_info", "failed"] = Field(
        description="ok: task done. need_info: one piece must be asked from the customer. "
                    "failed: the task cannot be done.")
    question_field: Optional[QuestionField] = Field(
        description="With need_info: the ONE piece to ask for.")
    question_text: Optional[str] = Field(
        description="With need_info: a short note for the Orchestrator (English) saying what "
                    "to ask; mention only times taken from tool results.")
    error_code: Optional[str] = Field(
        description="With failed: the tool error code, copied as written.")

    @model_validator(mode="after")
    def _need_info_names_its_question(self) -> "SubagentResult":
        if self.status == "need_info" and (self.question_field is None or not self.question_text):
            raise ValueError("need_info requires question_field and question_text")
        return self


class Extracted(BaseModel):
    """Những gì CODE trích từ ToolMessage của agent con — không từ chữ model."""

    facts: list[TurnFact] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    options: list[Moment] = Field(default_factory=list)
    proposal_created: bool = False


@dataclass
class RunContext:
    """Mọi thứ `build_tools`/`extract` của một agent con cần cho MỘT lần gọi."""

    db: AsyncIOMotorDatabase
    user: User
    snapshot: TurnSnapshot
    session: Optional[WaitingSession]   # phiên đang làm tiếp; None = việc mới
    request: str
    question: str
    turn_id: str
    service_ids: list[str]
    allow_propose: bool


@dataclass
class SubagentSpec:
    name: AgentName
    prompt: str
    build_tools: Callable[[RunContext], list[BaseTool]]
    extract: Callable[[list[BaseMessage], RunContext], Awaitable[Extracted]]


def is_transient(exc: BaseException) -> bool:
    """Lỗi TẠM THỜI — thử lại có thể khác: timeout, mất kết nối, 429, 5xx.
    400/401/403/404 và lỗi lập trình thì thử lại vẫn vậy."""
    if isinstance(exc, ModelError):
        return exc.is_retryable
    if isinstance(exc, (openai.APIConnectionError, httpx.TimeoutException,
                        httpx.TransportError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


# ---------------------------------------------------------------------------
# Human message
# ---------------------------------------------------------------------------


def _render_value(value: Any) -> str:
    """Giá trị memory → chữ. Danh sách `Moment` (dict iso/spoken) in cả hai
    dạng để model chép `iso` sang tool mà không tự tính lại."""
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    if isinstance(value, dict) and set(value) >= {"iso", "spoken"}:
        return f"{value['spoken']} ({value['iso']})"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_session(session: WaitingSession) -> str:
    """Khối `Session memory:` — chỉ dữ kiện có cấu trúc code đã lưu (spec §5)."""
    options = _render_value([option.model_dump() for option in session.question.options])
    lines = [
        "Session memory:",
        f"- original task: {session.task}",
        f"- waiting for: {session.question.field}, options={options}",
    ]
    for key in sorted(session.memory):
        lines.append(f"- {key}: {_render_value(session.memory[key])}")
    return "\n".join(lines)


def subagent_message(spec: SubagentSpec, ctx: RunContext) -> HumanMessage:
    """HumanMessage DUY NHẤT gửi agent con. Thứ tự cố định: bối cảnh (dòng đầu
    là hôm nay) → bộ nhớ phiên → lời dặn → câu khách dưới nhãn untrusted —
    chữ khách luôn đứng SAU nhãn, không ở đâu khác."""
    parts = [render_context(ctx.snapshot, audience=spec.name, turn_facts=[], memory_text=None,
                            now=now_utc())]
    if ctx.session is not None:
        parts.append(render_session(ctx.session))
    parts.append(f"Task: {ctx.request}")
    parts.append(f"{UNTRUSTED_LABEL}\n{ctx.question}")
    return HumanMessage(content="\n\n".join(parts))


# ---------------------------------------------------------------------------
# Chạy agent con
# ---------------------------------------------------------------------------


def _build_agent(spec: SubagentSpec, ctx: RunContext, middleware: Sequence[AgentMiddleware]):
    return create_agent(
        model=build_chat_model(tags=[spec.name], streaming=False, max_retries=0),
        tools=spec.build_tools(ctx),
        system_prompt=spec.prompt,
        middleware=[
            # Đếm lời gọi MODEL (≤ 4 vòng tool + lời gọi kết quả cuối) — không
            # dùng ToolCallLimitMiddleware: nó đếm cả tool call của
            # ToolStrategy nên agent dùng đủ 4 tool sẽ bị coi là lỗi.
            ModelCallLimitMiddleware(run_limit=settings.salon_subagent_max_rounds + 1,
                                     exit_behavior="error"),
            # `on_failure="error"`: mặc định "continue" trả một AIMessage tiếng
            # Anh chứa lỗi thay vì ném ra.
            ModelRetryMiddleware(max_retries=MODEL_RETRIES, retry_on=is_transient,
                                 on_failure="error", initial_delay=MODEL_RETRY_DELAY_SECONDS),
            # BudgetMiddleware (Task 13) vào đây — trong cùng, để mỗi lần thử
            # lại cũng được đếm vào `orch_llm_calls_per_user_per_hour`.
            *middleware,
        ],
        response_format=ToolStrategy(SubagentResult),
        name=spec.name,
        checkpointer=False,
    )


async def _invoke(agent, message: HumanMessage, *, timeout: float,
                  ) -> tuple[Optional[SubagentResult], list[BaseMessage], Optional[ErrorCode]]:
    """Chạy agent, giữ state MỚI NHẤT qua `astream(values)` — quá giờ giữa
    chừng vẫn còn các ToolMessage đã có (vd một đề xuất đã tạo), để `extract`
    không bỏ sót dữ kiện đã thật sự xảy ra trong DB."""
    latest: dict = {"messages": [message]}

    async def _drive() -> None:
        async for state in agent.astream({"messages": [message]}, stream_mode="values"):
            latest.update(state)

    error: Optional[ErrorCode] = None
    try:
        await asyncio.wait_for(_drive(), timeout)
    except asyncio.TimeoutError:
        error = "timeout"
    except StructuredOutputError:
        error = "schema_invalid"
    except Exception:
        # Lỗi model (đã hết lượt thử lại), chạm trần lời gọi model
        # (`ModelCallLimitExceededError`), lỗi tool ngoài dự kiến.
        error = "model_error"
    output = latest.get("structured_response")
    if error is None and not isinstance(output, SubagentResult):
        error = "schema_invalid"
    return (None if error else output), list(latest["messages"]), error


def _session(ctx: RunContext, spec: SubagentSpec, extracted: Extracted, *, field: str,
             options: list[Moment], text: str) -> WaitingSession:
    now = now_utc()
    return WaitingSession(
        user_id=str(ctx.user.id),
        agent=spec.name,
        # Làm tiếp thì giữ lời dặn GỐC; việc mới thì lời dặn mới (spec §5).
        task=ctx.session.task if ctx.session is not None else ctx.request,
        memory=extracted.memory,
        question=SessionQuestion(
            field=field, options=[SessionOption(**option.model_dump()) for option in options],
            text=text,
        ),
        asked_at=now,
        expires_at=now + timedelta(minutes=settings.salon_session_ttl_minutes),
        opened_turn_id=ctx.turn_id,
    )


async def _write_session(spec: SubagentSpec, ctx: RunContext, status: str,
                         result: Optional[SubagentResult], extracted: Extracted) -> bool:
    """Ghi phiên theo kết quả; trả True nếu đã ghi DB. `failed` giữ nguyên phiên cũ."""
    waiting = WaitingSessionService(ctx.db)
    if status == "need_info":
        await waiting.open(_session(ctx, spec, extracted, field=result.question_field,
                                    options=extracted.options, text=result.question_text))
        return True
    if status == "ok":
        if extracted.memory.get("next_requests"):
            # Đề xuất lịch đầu của "hai lịch một tin" (spec §5.1): phiên vẫn
            # mở để giữ yêu cầu kế, chờ khách xác nhận đề xuất.
            await waiting.open(_session(ctx, spec, extracted, field="confirm", options=[],
                                        text=(result.question_text if result else None) or ""))
        else:
            await waiting.close(str(ctx.user.id), spec.name, reason="ok")
        return True
    return False


def _summary(spec: SubagentSpec, status: str, result: Optional[SubagentResult],
             error: Optional[str], extracted: Extracted) -> str:
    """ToolMessage cho Orchestrator: JSON có cấu trúc, dữ kiện là của code."""
    return json.dumps({
        "agent": spec.name,
        "status": status,
        "question_field": result.question_field if result else None,
        "question_text": result.question_text if result else None,
        "error_code": error,
        "options": [option.spoken for option in extracted.options] if status == "need_info" else [],
        "facts": [fact.model_dump(mode="json") for fact in extracted.facts],
    }, ensure_ascii=False)


async def run_subagent(
    spec: SubagentSpec,
    *,
    request: str,
    continue_session: bool,
    service_ids: list[str],
    runtime: ToolRuntime[SalonContext, SalonState],
    db: AsyncIOMotorDatabase,
    user: User,
    holder: SnapshotHolder,
    middleware: Sequence[AgentMiddleware] = (),
) -> Command:
    """Chạy một agent con cho một tool giao việc của Orchestrator. `middleware`
    nhận thêm lớp đếm ngân sách (Task 13); mặc định không đếm."""
    context: SalonContext = runtime.context
    log_extra = {"user_id": str(user.id), "turn_id": context.turn_id,
                 "thread_id": context.thread_id, "agent": spec.name}

    snapshot = await holder.current()
    ctx = RunContext(
        db=db, user=user, snapshot=snapshot,
        session=snapshot.sessions.get(spec.name) if continue_session else None,
        request=request, question=context.question, turn_id=context.turn_id,
        service_ids=list(service_ids),
        # Một đề xuất mới mỗi lượt (spec §8): lượt đã có đề xuất thì tool từ chối.
        allow_propose=runtime.state.get("proposals_created", 0) < 1,
    )

    agent = _build_agent(spec, ctx, middleware)
    result, messages, error = await _invoke(agent, subagent_message(spec, ctx),
                                            timeout=settings.orch_task_timeout_seconds)
    extracted = await spec.extract(messages, ctx)
    if result is None and error is None:
        error = "schema_invalid"
    status = result.status if result is not None else "failed"
    if result is not None and result.status == "failed":
        error = result.error_code

    wrote = await _write_session(spec, ctx, status, result, extracted)
    # `failed` (nhất là quá giờ) có thể đã ghi DB giữa chừng mà không kịp có
    # ToolMessage — đọc lại bối cảnh cho chắc.
    if wrote or extracted.proposal_created or status == "failed":
        holder.mark_stale()

    logger.info("subagent_finished", extra={**log_extra, "status": status, "error_code": error,
                                            "proposal_created": extracted.proposal_created})

    facts = [*extracted.facts, AgentStatusFact(agent=spec.name, status=status)]
    update: dict[str, Any] = {
        "messages": [ToolMessage(content=_summary(spec, status, result, error, extracted),
                                 name=f"{spec.name}_task", tool_call_id=runtime.tool_call_id)],
        "turn_facts": [fact.model_dump(mode="json") for fact in facts],
    }
    if extracted.proposal_created:
        update["proposals_created"] = 1
    if status == "failed":
        update["failed_calls"] = [spec.name]
    return Command(update=update)
