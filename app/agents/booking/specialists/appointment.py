"""Appointment specialist agent (Task 2.4, §8.1 của thiết kế) — thực thi đúng
MỘT `TaskSpec` mà Orchestrator giao. Lưới ghi giữ nguyên: tool duy nhất có
thể TẠO gì đó là `propose_appointment`/`cancel_appointment`, và cả hai chỉ
tạo một PROPOSAL — chưa bao giờ ghi lịch (`ProposalService.approve` là nơi
ghi DUY NHẤT, nằm ngoài gói này).

`run_task` không tin chữ model khi chữ đó đáng ra phải đến từ DB/tool:
- (3) `proposal_id`/`start_at` model báo phải khớp ToolMessage THẬT và
  `ProposalService.get` — lệch thì `schema_invalid`, không đoán ý model.
- (4) `queued` ghi vào `ConversationStateService.set_queued` và trả về
  `data.queued` đều dựng lại TỪ JSON của tool, không phải từ `data` model trả.
- (5) `asked`/`nearest` khi tool báo lỗi nghiệp vụ (giờ đã có người đặt, ngoài
  giờ mở cửa...) đọc thẳng từ ToolMessage cuối của tool đề xuất — schema của
  model (`SpecialistOutput`) còn không có hai trường này, model không thể bịa.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from langchain.agents.middleware import AgentMiddleware

from langchain_core.messages import ToolMessage
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.agents.booking.context import TurnContext
from app.agents.booking.prompts.appointment import APPOINTMENT_PROMPT
from app.agents.booking.schemas import (Moment, ProposalCreated, TaskResult, TaskSpec,
                                         specialist_output_model)
from app.agents.booking.specialists.base import build_specialist, run_specialist
from app.agents.booking.specialists.guard import (OneProposalPerTaskMiddleware,
                                                    PROPOSAL_TOOLS, allowlist)
from app.agents.booking_graph.tools import make_booking_tools
from app.core.clock import ensure_aware, now_utc
from app.core.config import settings
from app.core.logging import get_logger
from app.models.conversation import QueuedRequest
from app.models.user import User
from app.services.conversation_state import ConversationStateService
from app.services.proposal import ProposalService

logger = get_logger(__name__)

# §8.1 nguyên văn — allowlist tool theo action, áp khi dựng agent cho MỖI task.
ALLOWLIST: dict[str, tuple[str, ...]] = {
    "resolve_time": ("parse_time",),
    "find_slots": ("parse_time", "find_free_slots"),
    "propose_book": ("parse_time", "find_free_slots", "propose_appointment"),
    "propose_move": ("parse_time", "find_free_slots", "propose_appointment"),
    "propose_cancel": ("cancel_appointment",),
    "list_appointments": ("list_my_appointments",),
}

# Mã lỗi nghiệp vụ `propose_appointment`/`cancel_appointment` trả về khi KHÔNG
# tạo được gì (giờ đã có người đặt, ngoài giờ mở cửa, ngày nghỉ, giờ đã qua,
# khách đã có đúng lịch đó, id lịch không có/không phải của khách) — những mã
# này đi kèm `asked`/`nearest` trong JSON tool, code đọc lại chứ không tin
# chữ model (model không có chỗ để bịa, `SpecialistOutput` không có hai
# trường này, nhưng vẫn kiểm cho chắc).
_TOOL_BUSINESS_ERRORS = frozenset({
    "slot_unavailable", "outside_hours", "closed_day", "time_passed",
    "already_yours", "appointment_not_found",
})


def _schema_invalid(spec: TaskSpec, *, attempts: int = 1) -> TaskResult:
    return TaskResult(
        task_id=spec.task_id, agent=spec.agent, action=spec.action,
        status="failed", error_code="schema_invalid", attempts=attempts,
    )


def _tool_json(message: ToolMessage) -> dict | None:
    try:
        data = json.loads(message.content)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _created_proposal_json(messages) -> dict | None:
    """JSON của ToolMessage đã TẠO đề xuất thành công trong task này (có
    `proposal_id`) — `OneProposalPerTaskMiddleware` đảm bảo có tối đa một,
    nên không cần phân biệt đầu/cuối danh sách."""
    for message in messages:
        if isinstance(message, ToolMessage) and message.name in PROPOSAL_TOOLS:
            data = _tool_json(message)
            if data is not None and "proposal_id" in data:
                return data
    return None


def _last_proposal_tool_json(messages) -> dict | None:
    """JSON của ToolMessage GẦN NHẤT thuộc `propose_appointment`/
    `cancel_appointment` — dùng khi tool báo lỗi nghiệp vụ, để lấy `asked`/
    `nearest` THẬT, không qua chữ model."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name in PROPOSAL_TOOLS:
            data = _tool_json(message)
            if data is not None:
                return data
    return None


def _moment_from(obj: dict | None) -> Moment | None:
    return Moment(**obj) if obj else None


async def _persist_queued(db: AsyncIOMotorDatabase, user: User, queued_json: list[dict]) -> list[Moment]:
    """(4) Ghi hàng chờ từ JSON của tool (không phải từ `data` model trả) và
    trả lại các `Moment` tương ứng cho `TaskResult.data.queued`."""
    now = now_utc()
    moments: list[Moment] = []
    requests: list[QueuedRequest] = []
    for index, item in enumerate(queued_json, start=1):
        at = item.get("at") or {}
        if "iso" not in at or "spoken" not in at:
            continue
        moment = Moment(iso=at["iso"], spoken=at["spoken"])
        moments.append(moment)
        requests.append(QueuedRequest(
            queue_id=f"q{index}",
            start_at=datetime.fromisoformat(moment.iso),
            spoken=moment.spoken,
            note=item.get("note"),
            queued_at=now,
        ))

    try:
        await ConversationStateService(db).set_queued(str(user.id), requests)
    except PyMongoError as exc:
        logger.warning("queued_persist_failed",
                       extra={"user_id": str(user.id), "count": len(requests), "error": str(exc)})
    return moments


async def run_task(
    db: AsyncIOMotorDatabase, user: User, spec: TaskSpec, ctx: TurnContext, question: str,
    *, extra_middleware: Sequence[AgentMiddleware] = (),
) -> TaskResult:
    """Chạy đúng một task của Appointment Agent.

    `extra_middleware` nối thêm vào cuối lưới của specialist — graph (Task
    5.3) truyền `BudgetMiddleware` của lượt qua đây để mỗi lời gọi model
    của task cũng ghi một dấu ngân sách LLM."""
    # (1) propose_move không có appointment_id là lỗi lập đích của Orchestrator
    # — không có gì để model làm, không tốn một lượt gọi model.
    if spec.action == "propose_move" and spec.args.appointment_id is None:
        return _schema_invalid(spec)

    # (2) dựng agent MỖI task: chỉ đúng tool theo action, và lưới một-đề-xuất.
    tools = allowlist(make_booking_tools(db, user), ALLOWLIST[spec.action])
    agent = build_specialist(
        prompt=APPOINTMENT_PROMPT, tools=tools, tag="appointment",
        output_model=specialist_output_model(spec.action),
        extra_middleware=[OneProposalPerTaskMiddleware(), *extra_middleware],
    )
    result, messages = await run_specialist(
        agent, spec, ctx, question, timeout=settings.orch_task_timeout_seconds,
    )

    created = _created_proposal_json(messages)

    if created is not None:
        # (3) model phải báo ĐÚNG proposal_id và start_at của đề xuất THẬT.
        if not isinstance(result.data, ProposalCreated) or result.data.proposal_id != created["proposal_id"]:
            return _schema_invalid(spec, attempts=result.attempts)
        stored = await ProposalService(db).get(user, created["proposal_id"])
        if stored is None:
            return _schema_invalid(spec, attempts=result.attempts)
        reported = ensure_aware(datetime.fromisoformat(result.data.start_at.iso))
        if ensure_aware(stored.start_at) != reported:
            return _schema_invalid(spec, attempts=result.attempts)

        # (4) hàng chờ — ghi và trả lại từ JSON tool, không từ model. Kiểm
        # SỰ CÓ MẶT của khoá `queued`, không phải giá trị: `queued: []` là
        # cách propose_appointment báo khách đã RÚT hết hàng chờ cũ — bỏ qua
        # (coi falsy = "không đổi gì") sẽ để hàng chờ cũ nằm lại
        # `conversations.state.queued` mãi mãi, vì `set_queued` không có nơi
        # gọi nào khác xoá nó.
        if "queued" in created:
            moments = await _persist_queued(db, user, created["queued"])
            result = result.model_copy(
                update={"data": result.data.model_copy(update={"queued": moments})}
            )
        return result

    if isinstance(result.data, ProposalCreated):
        # model nói đã tạo đề xuất nhưng không có ToolMessage nào làm chứng.
        return _schema_invalid(spec, attempts=result.attempts)

    # (5) lỗi nghiệp vụ của tool đề xuất — chép asked/nearest từ JSON tool.
    if result.status == "failed" and result.error_code in _TOOL_BUSINESS_ERRORS:
        tool_json = _last_proposal_tool_json(messages) or {}
        asked = _moment_from(tool_json.get("asked"))
        nearest = [Moment(**m) for m in tool_json.get("nearest", [])]
        result = result.model_copy(update={"asked": asked, "nearest": nearest})

    return result
