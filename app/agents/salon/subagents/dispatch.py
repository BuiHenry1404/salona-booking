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
import inspect
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
    question_fields: list[QuestionField] = Field(
        description="With need_info: every piece to ask for, all at once.")
    question_text: Optional[str] = Field(
        description="With need_info: a short note for the Orchestrator (English) saying what "
                    "to ask; mention only times taken from tool results.")
    error_code: Optional[str] = Field(
        description="With failed: the tool error code, copied as written.")

    @model_validator(mode="after")
    def _need_info_names_its_question(self) -> "SubagentResult":
        if self.status == "need_info" and (not self.question_fields or not self.question_text):
            raise ValueError("need_info requires question_fields and question_text")
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
    # Luật dịch vụ của đề xuất ĐẶT MỚI (Ruling R16; spec §4.1, §6.2) — code tính
    # trong `run_subagent`, chỉ `propose_appointment` đọc:
    # "unclear" = phiên Dịch vụ đang chờ / agent Dịch vụ mới nhất của lượt chưa chốt;
    # `require_service` = cuộc trò chuyện đã CHỐT dịch vụ (lượt trước hoặc lượt này).
    service_state: Literal["clear", "unclear"] = "clear"
    require_service: bool = False


@dataclass
class SubagentSpec:
    name: AgentName
    prompt: str
    # Đồng bộ, hoặc async khi enum phải đọc DB (agent Dịch vụ: id đang hoạt động).
    build_tools: Callable[[RunContext], list[BaseTool] | Awaitable[list[BaseTool]]]
    extract: Callable[[list[BaseMessage], RunContext], Awaitable[Extracted]]
    # Code đối chiếu `status` model tự điền với dữ kiện đã trích (bẫy 16) — chạy
    # ngay sau `extract`, trước khi ghi phiên chờ và dựng ToolMessage.
    reconcile: Optional[Callable[["SubagentResult", Extracted], "SubagentResult"]] = None


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
        f"- waiting for: {','.join(session.question.fields)}, options={options}",
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


async def tools_for(spec: SubagentSpec, ctx: RunContext) -> list[BaseTool]:
    """Tool của agent con cho MỘT lần gọi — `build_tools` đồng bộ hay async đều được."""
    tools = spec.build_tools(ctx)
    if inspect.isawaitable(tools):
        tools = await tools
    return list(tools)


def _build_agent(spec: SubagentSpec, tools: list[BaseTool], middleware: Sequence[AgentMiddleware]):
    return create_agent(
        model=build_chat_model(tags=[spec.name], streaming=False, max_retries=0),
        tools=tools,
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


async def _invoke(make_agent: Callable[[], Awaitable[Any]], message: HumanMessage, *,
                  timeout: float, log_extra: dict,
                  ) -> tuple[Optional[SubagentResult], list[BaseMessage], Optional[ErrorCode]]:
    """Dựng rồi chạy agent, giữ state MỚI NHẤT qua `astream(values)` — quá giờ
    giữa chừng vẫn còn các ToolMessage đã có (vd một đề xuất đã tạo), để
    `extract` không bỏ sót dữ kiện đã thật sự xảy ra trong DB. Dựng agent (có
    thể đọc DB cho enum) cũng nằm trong trần thời gian và cách quy lỗi."""
    latest: dict = {"messages": [message]}

    async def _drive() -> None:
        agent = await make_agent()
        async for state in agent.astream({"messages": [message]}, stream_mode="values"):
            latest.update(state)

    error: Optional[ErrorCode] = None
    try:
        await asyncio.wait_for(_drive(), timeout)
    except asyncio.TimeoutError:
        error = "timeout"
        logger.warning("subagent_timeout", extra={**log_extra, "timeout_seconds": timeout})
    except StructuredOutputError as exc:
        error = "schema_invalid"
        logger.warning("subagent_schema_invalid",
                       extra={**log_extra, "error_type": type(exc).__name__})
    except Exception as exc:
        # Lỗi model (đã hết lượt thử lại), chạm trần lời gọi model
        # (`ModelCallLimitExceededError`), lỗi tool ngoài dự kiến, lỗi DB lúc dựng tool.
        error = "model_error"
        # Có traceback để soi lỗi lập trình trong tool; không có chữ khách.
        logger.exception("subagent_error", extra={**log_extra, "error_type": type(exc).__name__})
    output = latest.get("structured_response")
    if error is None and not isinstance(output, SubagentResult):
        error = "schema_invalid"
    return (None if error else output), list(latest["messages"]), error


def _session(ctx: RunContext, spec: SubagentSpec, extracted: Extracted, *, fields: list[str],
             options: list[Moment], text: str) -> WaitingSession:
    now = now_utc()
    return WaitingSession(
        user_id=str(ctx.user.id),
        agent=spec.name,
        # Làm tiếp thì giữ lời dặn GỐC; việc mới thì lời dặn mới (spec §5).
        task=ctx.session.task if ctx.session is not None else ctx.request,
        memory=extracted.memory,
        question=SessionQuestion(
            fields=fields, options=[SessionOption(**option.model_dump()) for option in options],
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
        await waiting.open(_session(ctx, spec, extracted, fields=result.question_fields,
                                    options=extracted.options, text=result.question_text))
        return True
    if status == "ok":
        if extracted.memory.get("next_requests"):
            # Đề xuất lịch đầu của "hai lịch một tin" (spec §5.1): phiên vẫn
            # mở để giữ yêu cầu kế, chờ khách xác nhận đề xuất.
            await waiting.open(_session(ctx, spec, extracted, fields=["confirm"], options=[],
                                        text=(result.question_text if result else None) or ""))
        else:
            await waiting.close(str(ctx.user.id), spec.name, reason="ok")
        return True
    return False


def _session_service_ids(session: Optional[WaitingSession], passed: list[str],
                         log_extra: dict[str, Any]) -> list[str]:
    """Làm tiếp một phiên chờ: dịch vụ phiên được mở cùng THẮNG dịch vụ
    Orchestrator truyền (live run 4, lượt 8: phiên mở cho cắt tóc, Orchestrator
    truyền sơn gel, lịch 17:00 bị ghi nhầm sơn gel). Khách đổi dịch vụ là việc
    mới (`continue_session=false`), không đi qua đây."""
    if session is None:
        return passed
    kept = session.memory.get("service_ids")
    kept = [sid for sid in kept if isinstance(sid, str)] if isinstance(kept, list) else []
    if not kept or kept == passed:
        return passed
    logger.warning("subagent_service_ids_from_session",
                   extra={**log_extra, "passed": passed, "session": kept})
    return kept


def _service_state(snapshot: TurnSnapshot, turn_facts: list[dict]) -> Literal["clear", "unclear"]:
    """Phiên Dịch vụ đang chờ, hoặc lần gọi agent Dịch vụ MỚI NHẤT của lượt
    trả `need_info` / `failed` → "unclear" (gọi lại thành công thì thôi)."""
    if "service" in snapshot.sessions:
        return "unclear"
    statuses = [fact.get("status") for fact in turn_facts
                if fact.get("kind") == "agent_status" and fact.get("agent") == "service"]
    return "unclear" if statuses and statuses[-1] in ("need_info", "failed") else "clear"


def _service_identified(snapshot: TurnSnapshot, turn_facts: list[dict]) -> bool:
    """Cuộc trò chuyện đã CHỐT dịch vụ: lượt trước (`known_services`, chỉ dịch
    vụ đã chốt) hoặc `ServiceFact.identified` của lượt này."""
    return bool(snapshot.known_services) or any(
        fact.get("kind") == "service" and fact.get("identified") for fact in turn_facts)


def _summary(spec: SubagentSpec, status: str, result: Optional[SubagentResult],
             error: Optional[str], extracted: Extracted) -> str:
    """ToolMessage cho Orchestrator: JSON có cấu trúc, dữ kiện là của code."""
    return json.dumps({
        "agent": spec.name,
        "status": status,
        "question_fields": result.question_fields if result else None,
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
    turn_facts = list(runtime.state.get("turn_facts", []))
    session = snapshot.sessions.get(spec.name) if continue_session else None
    ids = _session_service_ids(session, list(service_ids), log_extra)
    ctx = RunContext(
        db=db, user=user, snapshot=snapshot,
        session=session,
        request=request, question=context.question, turn_id=context.turn_id,
        service_ids=ids,
        # Một đề xuất mới mỗi lượt (spec §8): lượt đã có đề xuất thì tool từ chối.
        allow_propose=runtime.state.get("proposals_created", 0) < 1,
        service_state=_service_state(snapshot, turn_facts),
        require_service=_service_identified(snapshot, turn_facts),
    )

    async def make_agent():
        return _build_agent(spec, await tools_for(spec, ctx), middleware)

    result, messages, error = await _invoke(make_agent, subagent_message(spec, ctx),
                                            log_extra=log_extra,
                                            timeout=settings.orch_task_timeout_seconds)
    extracted = await spec.extract(messages, ctx)
    if result is not None and spec.reconcile is not None:
        corrected = spec.reconcile(result, extracted)
        if corrected.status != result.status:
            logger.warning("subagent_status_corrected", extra={
                **log_extra, "from": result.status, "to": corrected.status})
        result = corrected
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
