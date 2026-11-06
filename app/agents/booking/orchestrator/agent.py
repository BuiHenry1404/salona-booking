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
from typing import Sequence

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
    # §11 `turn_ended.no_progress_streak` — chỉ đọc được từ state cuối của
    # guard; đường dựng lại từ ledger (timeout/lỗi) không có, để 0.
    no_progress_streak: int = 0


# Quyết định chủ dự án 06/11: Orchestrator chạy reasoning_effort="low".
# `build_chat_model` tự bỏ `temperature` khi có giá trị này (llm.py).
ORCH_REASONING_EFFORT = "low"


def build_orchestrator(deps: OrchestratorDeps, *, extra_middleware: Sequence[AgentMiddleware] = ()):
    """Dựng `create_agent` của Orchestrator, mới hoàn toàn cho một lượt —
    `deps` (và 5 tool `make_orchestrator_tools` dựng từ nó) đã đóng sẵn
    `db`/`user`/`ctx`/`ledger` của lượt này, không có state nào tái dùng giữa
    hai lượt (`checkpointer=False`).

    `extra_middleware` nối vào CUỐI danh sách — trong cùng nhất của chuỗi
    `awrap_model_call`, nên `BudgetMiddleware` (graph, Task 5.3) đếm đúng
    mỗi lần gọi model thật, kể cả lần `model_retry_middleware` thử lại."""
    model = build_chat_model(
        tags=["orchestrate"], streaming=False, max_retries=0,
        reasoning_effort=ORCH_REASONING_EFFORT,
    )
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
            *extra_middleware,
        ],
        checkpointer=False,
    )


def _render_turn(message: BaseMessage) -> str:
    # Chữ khách của các lượt TRƯỚC cũng là chữ khách — gắn nhãn untrusted
    # từng dòng (review M3.4 vòng 1, finding 3), không chỉ câu hỏi lượt này
    # (nhãn `UNTRUSTED` riêng, xem `orchestrator_message`).
    role = "Customer (untrusted)" if isinstance(message, HumanMessage) else "Assistant"
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
        no_progress_streak=state.get("no_progress_streak") or 0,
    )


# Vòng `finish`/`ask_customer` không bao giờ được `OrchestratorGuard` tính
# vào `rounds_executed` (nó tự nhảy END trước khi tới nhánh đếm vòng của
# `abefore_model` — xem `guard.py`) — dựng lại từ ledger phải khớp đúng, nên
# loại hai tool này khỏi tập "vòng" khi đếm lại (review M3.4 vòng 1, Minor).
_TERMINAL_TOOLS = frozenset({"finish", "ask_customer"})


async def _outcome_from_ledger(deps: OrchestratorDeps, *, reason: str, thread_id: str | None) -> OrchestrateOutcome:
    """Timeout hay lỗi model cắt ngang `agent.ainvoke` trước khi có state
    cuối — không có `Command` nào để đọc `finish_args`/`ask_args`/`rounds_executed`
    nữa (chúng chỉ sống trong state LangGraph đã mất), nhưng `OrchestratorGuard`
    đã `await` ghi mỗi `CallRecord` vào `turn_ledger` TRƯỚC khi trả kết quả
    cho model (§5.3/C1) — dựng lại outcome từ đó, không bịa phần đã mất.

    Ruling review M3.4 vòng 1 (§5.3(4)/§8.4/§10): `ended_by="error"` CHỈ khi
    ledger của lượt này KHÔNG có `CallRecord` nào `allowed=True` — nghĩa là
    chưa có gì xảy ra (kể cả KHÔNG có đề xuất đã ghi), lúc đó coi như lỗi
    "vòng đầu" đúng nghĩa spec. Ngược lại (đã có ít nhất một call được phép —
    ví dụ `decide_pending` vừa duyệt một lịch, hay `appointment_task` vừa tạo
    đề xuất) phải là `"limit"`: `"error"` ngụ ý A sẽ coi `pending_outcome` là
    "kept"/chưa có gì xảy ra, mâu thuẫn với một lịch ĐÃ ghi mà khách chưa
    được báo (anti-goal "lịch đã ghi mà khách không được báo" — `unannounced`
    ở lượt sau chỉ cứu được nếu `pending_outcome` đọc đúng). Áp dụng CÙNG
    luật cho cả timeout lẫn lỗi model (`reason` chỉ khác nhau để LOG, không
    đổi cách suy `ended_by`) — đây là chỗ reconcile spec §10 ("lỗi vòng đầu
    → error") với "timeout → limit" của brief Task 3.4: cả hai chỉ đúng khi
    chưa có call nào chạy được; có rồi thì luôn là `"limit"`."""
    doc = await deps.ledger.record_of(deps.turn_id)
    calls = [CallRecord.model_validate(c) for c in (doc or {}).get("calls") or []]
    pending_raw = (doc or {}).get("pending_outcome")
    has_allowed_call = any(c.allowed for c in calls)
    ended_by: EndedBy = "limit" if has_allowed_call else "error"

    user_id = str(deps.user.id) if deps.user is not None else deps.ctx.customer.id
    logger.error(
        "orchestrate_failed",
        extra={"reason": reason, "user_id": user_id, "turn_id": deps.turn_id, "thread_id": thread_id},
    )

    proposals_created = sum(
        1
        for c in calls
        if c.allowed and isinstance(c.result, TaskResult) and isinstance(c.result.data, ProposalCreated)
    )
    rounds_executed = len({c.round for c in calls if c.allowed and c.tool not in _TERMINAL_TOOLS})
    return OrchestrateOutcome(
        calls=calls,
        pending_outcome=PendingOutcome.model_validate(pending_raw) if pending_raw else None,
        ended_by=ended_by,
        finish=None,
        ask=None,
        proposals_created=proposals_created,
        rounds_executed=rounds_executed,
    )


async def run_orchestrate(
    deps: OrchestratorDeps,
    recent: list[BaseMessage],
    *,
    thread_id: str | None = None,
    extra_middleware: Sequence[AgentMiddleware] = (),
) -> OrchestrateOutcome:
    """Chạy Orchestrator đúng MỘT lượt, dưới trần `orch_orchestrate_timeout_seconds`.

    `thread_id` (nếu có) đi vào `config["configurable"]` của `agent.ainvoke`
    — đây là cách DUY NHẤT `OrchestratorGuard` (đọc `request.runtime.config`)
    gắn được `thread_id` vào log `orch_call`/`pending_decided` (§11).

    Timeout cắt ngang `agent.ainvoke` (model/tool đang treo), hoặc model ném
    lỗi (lỗi lập trình, hay `ModelRetryMiddleware` hết lượt thử lại và
    `on_failure="error"` ném lại) ở BẤT KỲ vòng nào — cả hai nhánh đọc lại
    `turn_ledger` để quyết `ended_by` theo CÙNG một luật (xem
    `_outcome_from_ledger`), không bịa ra một state không hề có.

    `cm.expired()` phân biệt timeout CỦA CHÍNH `asyncio.timeout` này với một
    `TimeoutError` khác bay ra từ bên trong (vd. một `asyncio.wait_for` nội
    bộ nào đó của specialist) — chỉ cái đầu mới được log `reason="timeout"`,
    còn lại coi như lỗi model (`reason="model_error"`)."""
    agent = build_orchestrator(deps, extra_middleware=extra_middleware)
    message = orchestrator_message(deps.ctx, recent, deps.question)
    # Mỗi vòng tốn NHIỀU bước đồ thị (before_model của guard, before_model của
    # `ToolRoundLimitMiddleware`, model, tool...), không phải 1 bước/vòng —
    # nhân rộng rãi để `recursion_limit` không bao giờ cắt ngang trước trần
    # vòng THẬT của guard (§7.1); timeout ở trên mới là lưới cắt lượt treo.
    recursion_limit = deps.max_rounds * 20 + 20
    config = {"recursion_limit": recursion_limit, "configurable": {"thread_id": thread_id}}

    try:
        async with asyncio.timeout(settings.orch_orchestrate_timeout_seconds) as cm:
            state = await agent.ainvoke({"messages": [message]}, config)
    except TimeoutError:
        reason = "timeout" if cm.expired() else "model_error"
        return await _outcome_from_ledger(deps, reason=reason, thread_id=thread_id)
    except Exception:
        return await _outcome_from_ledger(deps, reason="model_error", thread_id=thread_id)

    return _outcome_from_state(state)
