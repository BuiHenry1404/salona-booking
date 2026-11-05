"""`build_orchestrator`/`run_orchestrate` — dựng Orchestrator cho MỘT lượt,
chạy nó, trả `OrchestrateOutcome` (Task 3.4, §5.3/§7 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Ruling (sau Task 3.3): mỗi lượt dựng state MỚI — `agent.ainvoke` chỉ nhận
`{"messages": [...]}`, không mang `ended_by`/`force_end`/`no_progress_streak`/
`calls`… từ lượt trước (`checkpointer=False`, không thread nào giữ state giữa
hai lượt). `OrchestratorGuard` phải là middleware ĐẦU TIÊN có `before_model`
trong danh sách: `create_agent` nối các middleware có `before_model` theo
ĐÚNG thứ tự khai báo (`factory.py:1834-1852`, xem thêm hằng số
`middleware_w_before_model` dòng 1169) — middleware đầu tiên chạy trước, và
nó là middleware DUY NHẤT biết `ended_by`/`force_end`/trần vòng thật
(`deps.max_rounds`, §7.1) nên phải nhảy END trước. `ToolRoundLimitMiddleware`
sau nó chỉ là LƯỚI AN TOÀN thứ hai (đếm số lần GỌI MODEL, không phải số vòng
CHẠY ĐƯỢC như guard) — trần gấp đôi (`deps.max_rounds * 2`) để không bao giờ
chặn trước guard trong vận hành bình thường, chỉ cứu khi guard có lỗi logic
để vòng lặp chạy mãi (model gọi tool liên tục mà mọi call đều bị từ chối bởi
mã KHÔNG nằm trong `STREAK_CODES` — trường hợp lý thuyết, không nên xảy ra).
"""
from __future__ import annotations

import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

from app.agents.booking.context import UNTRUSTED, TurnContext, render_context
from app.agents.booking.orchestrator.guard import OrchestratorGuard
from app.agents.booking.orchestrator.state import OrchestratorState
from app.agents.booking.orchestrator.tools import OrchestratorDeps, make_orchestrator_tools
from app.agents.booking.prompts.orchestrator import ORCHESTRATOR_PROMPT
from app.agents.booking.schemas import (
    AskCustomerArgs,
    CallRecord,
    EndedBy,
    FinishArgs,
    PendingOutcome,
    ProposalCreated,
    TaskResult,
)
from app.agents.booking_graph.agents import model_retry_middleware
from app.agents.booking_graph.middleware.tools import ToolRoundLimitMiddleware
from app.agents.llm import build_chat_model
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# §5.4 nguyên văn: tối đa 2 cặp (khách, trợ lý) của lượt TRƯỚC đưa vào human
# message — Orchestrator không cần đọc lại cả ngày, chỉ cần vài câu gần nhất.
RECENT_TURNS = 2


class StrictToolsMiddleware(AgentMiddleware):
    """§12.17: 5 tool của Orchestrator strict-safe (không field nào `default`,
    không `conint`/`minimum`/`maximum`) nên bind `strict=True` an toàn — chỉ
    khi request thật sự có tool (agent này luôn có 5 tool, nhưng giữ kiểm để
    nhất quán với `NoParallelToolsMiddleware` của gói cũ)."""

    async def awrap_model_call(self, request: ModelRequest, handler):
        if request.tools:
            request = request.override(model_settings={**request.model_settings, "strict": True})
        return await handler(request)


class OrchestrateOutcome(BaseModel):
    """Kết quả MỘT lượt Orchestrator — đọc từ state cuối của `agent.ainvoke`
    (hoặc, khi lượt bị timeout/lỗi model trước khi có state cuối, dựng lại từ
    `turn_ledger` đã ghi — xem `run_orchestrate`)."""

    calls: list[CallRecord]
    pending_outcome: PendingOutcome | None
    ended_by: EndedBy
    finish: FinishArgs | None
    ask: AskCustomerArgs | None
    proposals_created: int
    rounds_executed: int


def build_orchestrator(deps: OrchestratorDeps):
    """Dựng `create_agent` của Orchestrator, mới hoàn toàn cho một lượt —
    `deps` (và 5 tool `make_orchestrator_tools` dựng từ nó) đã đóng sẵn
    `db`/`user`/`ctx`/`ledger` của lượt này, không có state nào tái dùng giữa
    hai lượt (`checkpointer=False`)."""
    model = build_chat_model(tags=["orchestrate"], streaming=False, max_retries=0)
    return create_agent(
        model=model,
        tools=make_orchestrator_tools(deps),
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=OrchestratorState,
        middleware=[
            # Phải đứng đầu — xem docstring module.
            OrchestratorGuard(
                deps,
                max_calls_per_round=settings.orch_max_calls_per_round,
                no_progress_limit=settings.orch_no_progress_streak,
            ),
            StrictToolsMiddleware(),
            ToolRoundLimitMiddleware(deps.max_rounds * 2),
            model_retry_middleware(),
        ],
        checkpointer=False,
    )


def _render_turn(message: BaseMessage) -> str:
    role = "Customer" if isinstance(message, HumanMessage) else "Assistant"
    content = message.content if isinstance(message.content, str) else str(message.content)
    return f"{role}: {content}"


def orchestrator_message(ctx: TurnContext, recent: list[BaseMessage], question: str) -> HumanMessage:
    """Human message DUY NHẤT gửi Orchestrator: khối bối cảnh đầy đủ, rồi tối
    đa `RECENT_TURNS` cặp (khách, trợ lý) gần nhất rút từ
    `app.agents.booking_graph.history.model_history`, rồi câu khách dưới nhãn
    `UNTRUSTED` (luôn SAU nhãn, không bao giờ đứng trước — như
    `specialists/base.py::task_message`)."""
    windowed = recent[-(RECENT_TURNS * 2):] if recent else []
    lines = ["Last turns:", *(_render_turn(m) for m in windowed)]
    content = (
        f"{render_context(ctx, 'full')}\n\n"
        f"{chr(10).join(lines)}\n\n"
        f"{UNTRUSTED}{question}"
    )
    return HumanMessage(content=content)


def _outcome_from_state(state: dict) -> OrchestrateOutcome:
    """Đọc `OrchestrateOutcome` từ state cuối cùng `agent.ainvoke` trả về.

    `ended_by` thiếu nghĩa là lượt dừng do `ToolRoundLimitMiddleware` (lưới
    an toàn thứ hai, không biết trường `ended_by`) chứ không phải guard —
    cả hai đường đều là trần vòng, nên coi như "limit" (ruling brief)."""
    pending_raw = state.get("pending_outcome")
    finish_raw = state.get("finish_args")
    ask_raw = state.get("ask_args")
    return OrchestrateOutcome(
        calls=[CallRecord.model_validate(c) for c in state.get("calls") or []],
        pending_outcome=PendingOutcome.model_validate(pending_raw) if pending_raw else None,
        ended_by=state.get("ended_by") or "limit",
        finish=FinishArgs.model_validate(finish_raw) if finish_raw else None,
        ask=AskCustomerArgs.model_validate(ask_raw) if ask_raw else None,
        proposals_created=state.get("proposals_created") or 0,
        rounds_executed=state.get("rounds_executed") or 0,
    )


async def _outcome_from_ledger(deps: OrchestratorDeps, ended_by: EndedBy) -> OrchestrateOutcome:
    """Timeout hay lỗi model cắt ngang `agent.ainvoke` trước khi có state
    cuối — không có `Command` nào để đọc `finish_args`/`ask_args`/`rounds_executed`
    nữa (chúng chỉ sống trong state LangGraph đã mất), nhưng `OrchestratorGuard`
    đã `await` ghi mỗi `CallRecord` vào `turn_ledger` TRƯỚC khi trả kết quả
    cho model (§5.3/C1) — dựng lại outcome từ đó, không bịa phần đã mất."""
    doc = await deps.ledger.record_of(deps.turn_id)
    calls = [CallRecord.model_validate(c) for c in (doc or {}).get("calls") or []]
    pending_raw = (doc or {}).get("pending_outcome")
    proposals_created = sum(
        1
        for c in calls
        if c.allowed and isinstance(c.result, TaskResult) and isinstance(c.result.data, ProposalCreated)
    )
    rounds_executed = len({c.round for c in calls if c.allowed})
    return OrchestrateOutcome(
        calls=calls,
        pending_outcome=PendingOutcome.model_validate(pending_raw) if pending_raw else None,
        ended_by=ended_by,
        finish=None,
        ask=None,
        proposals_created=proposals_created,
        rounds_executed=rounds_executed,
    )


async def run_orchestrate(deps: OrchestratorDeps, recent: list[BaseMessage]) -> OrchestrateOutcome:
    """Chạy Orchestrator đúng MỘT lượt, dưới trần `orch_orchestrate_timeout_seconds`.

    Timeout cắt ngang `agent.ainvoke` (model/tool đang treo) → `ended_by="limit"`.
    Model ném lỗi (vd. lỗi lập trình, hay `ModelRetryMiddleware` hết lượt thử
    lại và `on_failure="error"` ném lại) ở BẤT KỲ vòng nào, kể cả vòng đầu
    (chưa có `ToolMessage` nào) → `ended_by="error"`. Cả hai nhánh đọc lại
    những gì `turn_ledger` đã kịp ghi thay vì bịa ra một state không hề có."""
    agent = build_orchestrator(deps)
    message = orchestrator_message(deps.ctx, recent, deps.question)
    # Mỗi vòng tốn NHIỀU bước đồ thị (before_model của guard, before_model của
    # `ToolRoundLimitMiddleware`, model, tool...), không phải 1 bước/vòng —
    # nhân rộng rãi để `recursion_limit` không bao giờ cắt ngang trước trần
    # vòng THẬT của guard (§7.1); timeout ở trên mới là lưới cắt lượt treo.
    recursion_limit = deps.max_rounds * 20 + 20

    try:
        async with asyncio.timeout(settings.orch_orchestrate_timeout_seconds):
            state = await agent.ainvoke(
                {"messages": [message]}, {"recursion_limit": recursion_limit}
            )
    except TimeoutError:
        logger.warning("orchestrate_timeout", extra={"turn_id": deps.turn_id})
        return await _outcome_from_ledger(deps, "limit")
    except Exception:
        logger.exception("orchestrate_model_error", extra={"turn_id": deps.turn_id})
        return await _outcome_from_ledger(deps, "error")

    return _outcome_from_state(state)
