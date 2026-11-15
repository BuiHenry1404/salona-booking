"""Tool của Orchestrator (Task 11, spec §3.1, §4.1; docs/architech-agent.md
luật 3, 6).

Orchestrator KHÔNG có tool nghiệp vụ riêng: ba tool giao việc cho agent con
(`appointment_task`, `shop_info_task`, `service_task`) chỉ khai
schema rồi gọi `run_subagent`, và `decide_pending` đưa nhãn của model qua
`veto` rồi `apply_decision` — đường ghi duy nhất cho chữ khách gõ.

Schema CỐ ĐỊNH mọi lượt (không enum dựng theo lượt, không giá trị mặc định;
tham số tuỳ chọn là `X | None`) để phần đầu prompt của Orchestrator trúng
prompt cache. Id dịch vụ, phiên chờ, đề xuất chờ... do `ToolRulesMiddleware`
kiểm bằng code TRƯỚC khi tool chạy (`middleware/tool_rules.py`).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Optional

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import Field

from app.agents.salon.orchestrator.decide import Aside, DecideLabel, apply_decision, veto
from app.agents.salon.orchestrator.middleware.budget import BudgetMiddleware
from app.agents.salon.orchestrator.state import SalonContext, SalonState
from app.agents.salon.shared.context import SnapshotHolder, TurnSnapshot
from app.agents.salon.shared.schemas import DecisionFact
# `CONTROL_TOOLS` sống ở `shared/` (guard cần nó mà không được import
# `orchestrator/`); re-export ở đây cho code/test cũ đọc nó từ tools.
from app.agents.salon.shared.tool_names import CONTROL_TOOLS  # noqa: F401
from app.agents.salon.subagents.appointment.agent import APPOINTMENT
from app.agents.salon.subagents.dispatch import run_subagent
from app.agents.salon.subagents.service.agent import SERVICE
from app.agents.salon.subagents.shop_info.agent import SHOP_INFO
from app.agents.salon.turn.ledger import TurnLedger
from app.core.logging import get_logger
from app.models.user import SelfAddress, User
from app.repositories.user import UserRepository

logger = get_logger(__name__)

_REQUEST_DESCRIPTION = (
    "Short English instruction for the agent: what the customer wants done. Never put ids, "
    "ISO times or computed dates here; the agent reads times itself."
)
_CONTINUE_DESCRIPTION = (
    "true: the customer is answering this agent's waiting question (its waiting session). "
    "false: a new task; replaces any waiting session of this agent."
)


def protected_moments(snapshot: TurnSnapshot) -> list[datetime]:
    """Giờ đang GIỮ cho yêu cầu kế (`memory.next_requests` của phiên Đặt lịch,
    spec §5.1): khách nhắc lại các giờ này khi đồng ý đề xuất đầu không phải
    là đổi ý — `veto` không được hạ `approve` thành `change` vì chúng."""
    session = snapshot.sessions.get("appointment")
    if session is None:
        return []
    return [datetime.fromisoformat(item["iso"]) for item in session.memory.get("next_requests", [])]


def _error(runtime: ToolRuntime[SalonContext, SalonState], code: str, hint: str) -> ToolMessage:
    return ToolMessage(content=json.dumps({"error_code": code, "hint": hint}),
                       name="decide_pending", tool_call_id=runtime.tool_call_id, status="error")


def make_orchestrator_tools(
    db: AsyncIOMotorDatabase, user: User, holder: SnapshotHolder,
) -> list[BaseTool]:
    """Tool của Orchestrator cho MỘT lượt. Chỉ phần thân tool đóng gói theo
    lượt (`db`, `user`, `holder`); schema không phụ thuộc lượt nào."""

    def _budget() -> list[BudgetMiddleware]:
        # Lời gọi model của agent con cũng tính vào `orch_llm_calls_per_user_per_hour`
        # (spec §3.3); `run_subagent` đặt nó trong cùng, dưới retry.
        return [BudgetMiddleware(db, str(user.id))]

    @tool
    async def appointment_task(
        request: Annotated[str, Field(description=_REQUEST_DESCRIPTION)],
        continue_session: Annotated[bool, Field(description=_CONTINUE_DESCRIPTION)],
        service_ids: Annotated[Optional[list[str]], Field(
            description="Service ids already identified in this conversation (facts, waiting "
                        "sessions, the pending proposal). null when none is known.")],
        runtime: ToolRuntime[SalonContext, SalonState],
    ) -> Command:
        """Give a task to the Appointment agent: find free times, list the customer's
        appointments, or propose booking / moving / cancelling one. It only creates a
        proposal; the customer confirms it on a later turn."""
        # `run_subagent` tự `holder.mark_stale()` khi agent con tạo đề xuất / mở phiên.
        return await run_subagent(
            APPOINTMENT, request=request, continue_session=continue_session,
            service_ids=list(service_ids or []), runtime=runtime, db=db, user=user, holder=holder,
            middleware=_budget(),
        )

    @tool
    async def shop_info_task(
        request: Annotated[str, Field(description=_REQUEST_DESCRIPTION)],
        continue_session: Annotated[bool, Field(description=_CONTINUE_DESCRIPTION)],
        runtime: ToolRuntime[SalonContext, SalonState],
    ) -> Command:
        """Give a task to the Shop info agent: opening hours, closed days, whether the shop
        is open at a given time, or whether it is busy right now."""
        return await run_subagent(
            SHOP_INFO, request=request, continue_session=continue_session, service_ids=[],
            runtime=runtime, db=db, user=user, holder=holder, middleware=_budget(),
        )

    @tool
    async def service_task(
        request: Annotated[str, Field(description=_REQUEST_DESCRIPTION)],
        continue_session: Annotated[bool, Field(description=_CONTINUE_DESCRIPTION)],
        asks_price: Annotated[bool, Field(
            description="true when the customer asks about a price in this message; "
                        "false otherwise.")],
        runtime: ToolRuntime[SalonContext, SalonState],
    ) -> Command:
        """Give a task to the Services agent: find the service the customer named, its
        price range and duration, or the price list. Its facts carry the service ids to
        pass to appointment_task."""
        command = await run_subagent(
            SERVICE, request=request, continue_session=continue_session, service_ids=[],
            runtime=runtime, db=db, user=user, holder=holder, middleware=_budget(),
        )
        if asks_price:
            # Code không đoán ý khách từ chữ (bẫy 21), nhưng Orchestrator hiểu ý
            # khách — đánh dấu để kế hoạch báo giá dù lượt có đặt lịch hay không
            # (spec "Bổ sung: asks_price"). Chỉ đánh dấu dịch vụ khách NÊU
            # (`identified`) — khách hỏi bảng giá (nhiều dịch vụ chỉ được liệt
            # kê, không chốt) không bắt kế hoạch báo giá từng dịch vụ liệt kê
            # (quyết định của controller).
            for fact in command.update.get("turn_facts", []):
                if fact.get("kind") == "service" and fact.get("identified"):
                    fact["asked_price"] = True
        return command

    @tool
    async def decide_pending(
        label: Annotated[DecideLabel, Field(
            description="What the customer's message means for the pending proposal: approve "
                        "(agrees), reject (declines), change (wants another time), unclear "
                        "(cannot tell), none (talks about something else).")],
        aside: Annotated[Optional[Aside], Field(
            description="relative_free: agrees only 'if free' / relative to something; "
                        "defer_only: wants to decide later; defer_with_request: decide later "
                        "and asks something else. null otherwise.")],
        reason: Annotated[str, Field(description="One short English sentence: why this label.")],
        runtime: ToolRuntime[SalonContext, SalonState],
    ) -> Command | ToolMessage:
        """Record the customer's decision on the pending proposal. Call it once per turn,
        before any new appointment task, whenever a proposal is pending."""
        context: SalonContext = runtime.context
        snapshot = await holder.current()
        pending = snapshot.pending
        if pending is None:
            # Lưới an toàn — `ToolRulesMiddleware` đã chặn trường hợp này (`no_pending`).
            return _error(runtime, "no_pending", "There is no pending proposal to decide.")

        checked = veto(label, aside, context.question, pending, context.asked_at,
                       protected=protected_moments(snapshot))
        if checked.reject_code is not None:
            return _error(runtime, checked.reject_code,
                          "The customer only repeated the proposed time; that is not a change. "
                          "Choose another label.")

        outcome = await apply_decision(
            db, user, pending=pending, label=checked.label, turn_id=context.turn_id,
            asked_at=context.asked_at, ledger=TurnLedger(db), thread_id=context.thread_id,
        )
        holder.mark_stale()
        logger.info("pending_decided", extra={
            "user_id": str(user.id), "turn_id": context.turn_id, "thread_id": context.thread_id,
            "label": label, "applied_label": checked.label, "veto_reason": checked.reason,
            "outcome": outcome.outcome,
        })

        fact = DecisionFact(outcome=outcome.outcome, proposal_kind=outcome.kind, at=outcome.at,
                            error=outcome.error)
        summary = {
            "outcome": outcome.outcome,
            "applied_label": checked.label,
            "veto_reason": checked.reason,
            "proposal_kind": outcome.kind,
            "at": outcome.at.spoken,
            "error": outcome.error,
            "nearest_free": [moment.spoken for moment in outcome.nearest],
        }
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(summary, ensure_ascii=False),
                                     name="decide_pending", tool_call_id=runtime.tool_call_id)],
            "pending_decided": True,
            "pending_outcome": outcome.model_dump(mode="json"),
            "decide_calls": runtime.state.get("decide_calls", 0) + 1,
            "turn_facts": [fact.model_dump(mode="json")],
        })

    @tool
    async def remember_address(
        form: Annotated[SelfAddress, Field(
            description="How the customer said they want to be addressed.")],
        runtime: ToolRuntime[SalonContext, SalonState],
    ) -> Command:
        """Record how the customer wants to be addressed. Call it only when the customer
        says this themselves (for example they call themselves aunt when booking, or say
        "just call me uncle") — never guess it from their name or writing style. Call it
        again if they later ask to be addressed differently."""
        # Ghi vào DB (memory, sống qua mọi cuộc trò chuyện) VÀ lên chính `User`
        # object `holder` đang giữ — `load_snapshot` không đọc lại user (chỉ
        # nhận nó qua tham số), nên không sửa object này thì `mark_stale()`
        # vẫn dựng lại bối cảnh với `self_address` cũ.
        await UserRepository(db).set_self_address(str(user.id), form)
        user.self_address = form
        holder.mark_stale()
        return Command(update={
            "messages": [ToolMessage(content=json.dumps({"ok": True}),
                                     name="remember_address", tool_call_id=runtime.tool_call_id)],
        })

    return [appointment_task, shop_info_task, service_task, decide_pending, remember_address]
