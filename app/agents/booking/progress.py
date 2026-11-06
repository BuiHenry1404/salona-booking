"""`step_goal`/`turn_facts` — tiến độ mục tiêu xuyên lượt và `TurnFacts` cho
`ConversationStateService.apply_turn_analysis` (§7.2, §8.3, Task 5.1).

Hàm thuần: không gọi LLM, không ghi DB, không từ điển từ khoá ý định — chỉ
nhìn `CallRecord`/`PendingOutcome` đã có sẵn trong `OrchestrateOutcome` (chính
`OrchestratorGuard` đã tính `markers`/`pending_outcome`, ở đây không tính lại).
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from app.agents.booking.context import TurnContext
from app.agents.booking.resolver import ResolveResult
from app.agents.booking.schemas import CallRecord, TurnFacts
from app.core.config import settings
from app.models.conversation import GoalProgress
from app.services.conversation_state import clean_free_text_item

if TYPE_CHECKING:  # tránh vòng import: orchestrator/agent.py không cần biết progress.py.
    from app.agents.booking.orchestrator.agent import OrchestrateOutcome

# Nhãn `PendingOutcome.outcome` coi là "dứt khoát" — đóng mục tiêu (§7.2).
_DECISIVE_OUTCOMES = frozenset({"approved", "rejected"})
_DEFER_ASIDES = frozenset({"defer_only", "defer_with_request"})


def _appointment_task_args(call: "CallRecord") -> dict:
    """`service`/`appointment_id` model gửi trong một call `appointment_task`
    ĐƯỢC CHẠY — đọc lại từ `args_norm` (JSON đã chuẩn hoá, `reason`/`hint`
    đã bị bỏ, không chạm chữ khách thô)."""
    try:
        parsed = json.loads(call.args_norm)
    except (TypeError, ValueError):
        return {}
    inner = parsed.get("args") if isinstance(parsed, dict) else None
    return inner if isinstance(inner, dict) else {}


def _decide_pending_aside(call: "CallRecord") -> str | None:
    """`aside` model gửi trong một call `decide_pending` ĐƯỢC CHẠY."""
    try:
        parsed = json.loads(call.args_norm)
    except (TypeError, ValueError):
        return None
    return parsed.get("aside") if isinstance(parsed, dict) else None


def _new_goal(goal_id: str, args: dict) -> GoalProgress:
    return GoalProgress(
        goal_id=goal_id,
        turns_without_progress=0,
        last_marker=None,
        service=args.get("service"),
        appointment_id=args.get("appointment_id"),
    )


def step_goal(
    goal: GoalProgress,
    outcome: "OrchestrateOutcome | None",
    resolve: ResolveResult,
    ctx: TurnContext,
) -> tuple[GoalProgress, bool]:
    """§7.2 — mở/đóng `GoalProgress.goal_id`, đếm/reset `turns_without_progress`.

    `outcome` là `None` ở đường nút bấm (R1, không chạy Orchestrator) — lượt
    đó không có call `appointment_task` nào để xét, nên trung lập (không
    đổi `goal`, `limit_hit=False`). `resolve` không dùng trong luật §7.2 —
    tham số giữ cho hợp đồng cân với `turn_facts` (cùng chữ ký đầu vào)."""
    if outcome is None:
        return goal, False

    calls = outcome.calls
    appointment_calls = [c for c in calls if c.tool == "appointment_task" and c.allowed]
    decide_calls = [c for c in calls if c.tool == "decide_pending" and c.allowed]

    pending_outcome = outcome.pending_outcome
    kept_aside: str | None = None
    if pending_outcome is not None and pending_outcome.outcome == "kept" and decide_calls:
        kept_aside = _decide_pending_aside(decide_calls[-1])

    closes = pending_outcome is not None and (
        pending_outcome.outcome in _DECISIVE_OUTCOMES
        or (pending_outcome.outcome == "kept" and kept_aside == "defer_only")
    )

    # "đổi mục tiêu": goal đã mở, một call appointment_task nêu service hoặc
    # appointment_id KHÁC HẲN cái đang theo dõi -> đóng cái cũ, mở cái mới.
    new_target: dict | None = None
    if goal.goal_id is not None and not closes:
        for call in appointment_calls:
            args = _appointment_task_args(call)
            service = args.get("service")
            appointment_id = args.get("appointment_id")
            service_changed = service is not None and service != goal.service
            appointment_changed = appointment_id is not None and appointment_id != goal.appointment_id
            if service_changed or appointment_changed:
                new_target = args
                break

    if closes:
        return GoalProgress(), False
    if new_target is not None:
        return _new_goal(uuid.uuid4().hex, new_target), False

    if goal.goal_id is None and appointment_calls:
        goal = _new_goal(uuid.uuid4().hex, _appointment_task_args(appointment_calls[0]))

    kept_defer = pending_outcome is not None and pending_outcome.outcome == "kept" and kept_aside in _DEFER_ASIDES
    if not appointment_calls or kept_defer:
        return goal, False

    new_markers = [m for c in calls for m in c.markers]
    if new_markers:
        # TIẾN TRIỂN thật — reset về 0.
        goal = goal.model_copy(update={"turns_without_progress": 0, "last_marker": new_markers[-1]})
        return goal, False

    # Ruling (review Task 5.1, round 2): hỏi field MỚI là TRUNG LẬP, không
    # reset — `ctx.open_question` chỉ nhớ field của LƯỢT TRƯỚC, nên Orchestrator
    # hỏi một field khác mỗi lượt sẽ reset mãi, đề tài không bao giờ tới
    # `orch_goal_turns_limit` dù bế tắc thật. Không marker thì không reset, dù
    # có hỏi field mới hay không — chỉ KHÔNG CỘNG (không phạt việc hỏi field
    # mới, cũng không coi đó là tiến triển).
    first_ask_new_field = (
        outcome.ended_by == "ask_customer"
        and outcome.ask is not None
        and (ctx.open_question is None or outcome.ask.field != ctx.open_question.field)
    )
    if first_ask_new_field:
        return goal, False

    turns_without_progress = goal.turns_without_progress + 1
    limit_hit = turns_without_progress >= settings.orch_goal_turns_limit
    goal = goal.model_copy(update={"turns_without_progress": 0 if limit_hit else turns_without_progress})
    return goal, limit_hit


def turn_facts(
    outcome: "OrchestrateOutcome | None",
    resolve: ResolveResult,
    ctx: TurnContext,
) -> TurnFacts:
    """§8.3 — ba trường `ConversationStateService.apply_turn_analysis` đọc.

    `outcome` là `None` ở đường nút bấm: không có `finish(...)`, nên
    `service`/`stated` rỗng; `answers_question` chỉ có thể đúng khi `resolve`
    đi qua R3 (chỉ xảy ra ở đường chữ, `outcome` luôn có mặt lúc đó)."""
    service: str | None = None
    stated: list[str] = []
    finish = outcome.finish if outcome is not None else None
    if finish is not None:
        service = clean_free_text_item(finish.service)
        cleaned = [clean_free_text_item(s) for s in finish.stated]
        stated = [s for s in cleaned if s is not None][:2]

    ask = outcome.ask if outcome is not None else None
    answers_question = resolve.rule == "R3" and (
        ask is None or ctx.open_question is None or ask.field != ctx.open_question.field
    )

    return TurnFacts(service=service, stated=stated, answers_question=answers_question)
