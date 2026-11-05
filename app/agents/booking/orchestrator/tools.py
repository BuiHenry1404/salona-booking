"""5 tool điều khiển của Orchestrator (§5.1) và `OrchestratorDeps`.

Orchestrator không bao giờ tự tạo/ghi gì: nó chỉ chọn `action`/`args`.
`appointment_task`/`shop_info_task` giao việc cho hai specialist (hàm thuần
`run_task(db, user, spec, ctx, question) -> TaskResult`), `decide_pending`
giao cho `decide.apply_decision` (Task 3.2 lấp đầy — chữ ký đã cố định),
`ask_customer`/`finish` chỉ đánh dấu kết thúc vòng (`return_direct=True`,
§5.6) — không tool nào ở đây chạm `ProposalService`/`AppointmentService`
trực tiếp (`tests/test_write_fence.py`).

Tham số thứ hai của `appointment_task`/`shop_info_task` PHẢI tên "args" theo
§5.1 (prompt sẽ gọi đúng tên này) — `@tool` suy luận chữ ký từ signature
Python coi tham số tên "args" như `*args` (va chạm với
`BaseTool.args`/cách LangChain dựng schema), nên hai tool này dựng bằng
`StructuredTool.from_function(..., args_schema=...)` với schema soạn tay
thay vì để suy luận tự động.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from langchain_core.tools import BaseTool, StructuredTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import create_model

from app.agents.booking.context import TurnContext
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.orchestrator.decide import apply_decision
from app.agents.booking.schemas import (
    AppointmentAction,
    Aside,
    CallRecord,
    DecideLabel,
    ProposalCreated,
    QuestionField,
    ShopAction,
    ShopArgs,
    TaskResult,
    TaskSpec,
    appointment_args_model,
)
from app.agents.booking.specialists.appointment import run_task as _appointment_run_task
from app.agents.booking.specialists.shop_info import run_task as _shop_info_run_task
from app.core.clock import now_utc
from app.models.user import User

RunTask = Callable[[TaskSpec, TurnContext, str], Awaitable[TaskResult]]

# Nhóm (g) (dùng ở `OrchestratorGuard`, task sau): các tool "phải đi một
# mình" trong một vòng. `propose_*` không phải tên tool riêng — cả ba action
# đề xuất (`propose_book`/`propose_move`/`propose_cancel`) đều gọi chung
# `appointment_task`, nên middleware nhận ra chúng từ `action`, không từ tên
# tool; hằng số này chỉ giữ chỗ cho nhánh đó.
SEQUENTIAL = frozenset({"appointment_task:propose", "decide_pending", "ask_customer", "finish"})


@dataclass
class OrchestratorDeps:
    """Phụ thuộc một lượt của Orchestrator — mọi thứ 5 tool cần để chạy,
    không giữ state LangGraph (đó là việc của middleware/guard)."""

    db: AsyncIOMotorDatabase
    user: User
    ctx: TurnContext
    ledger: TurnLedger
    turn_id: str
    question: str
    max_rounds: int
    # Mặc định đóng `db`/`user` của chính deps (`specialists.*.run_task`) —
    # test truyền hàm giả để không chạm Mongo/Azure.
    run_appointment: RunTask | None = None
    run_shop: RunTask | None = None
    task_counter: "itertools.count[int]" = field(default_factory=itertools.count)

    def __post_init__(self) -> None:
        if self.run_appointment is None:
            self.run_appointment = self._default_run_appointment
        if self.run_shop is None:
            self.run_shop = self._default_run_shop

    async def _default_run_appointment(self, spec: TaskSpec, ctx: TurnContext, question: str) -> TaskResult:
        return await _appointment_run_task(self.db, self.user, spec, ctx, question)

    async def _default_run_shop(self, spec: TaskSpec, ctx: TurnContext, question: str) -> TaskResult:
        return await _shop_info_run_task(self.db, self.user, spec, ctx, question)


APPOINTMENT_TASK_DESCRIPTION = """Run one appointment sub-task and return its TaskResult as JSON.

Use this for anything about the customer's own appointments: resolving a time
the customer said, finding free slots, proposing to book/move/cancel, or
listing their appointments. One call = one `action`; chain several calls
(in the same or later rounds) when a request needs more than one step (e.g.
resolve_time then propose_book).

`args.time.anchor`, when you set it, WINS over the specialist's own implicit
anchor (it only falls back to guessing when `anchor` is null) — set it to
"pending"/"declined"/"appointment"/"queued" (with `queue_id`) whenever the
customer's time refers to one of those, instead of leaving `anchor` null and
hoping the specialist guesses right.

Never call this alongside `decide_pending` when a proposal is pending:
`decide_pending` must run first so the pending proposal is resolved before a
new one is proposed. Do not call this together with `finish`/`ask_customer`
in the same round — ending the turn always happens alone."""

SHOP_INFO_TASK_DESCRIPTION = """Run one shop-info sub-task and return its TaskResult as JSON.

Use this for questions about the salon itself: opening hours, whether the
owner is currently busy, or whether the salon is open at one specific moment
(`is_open_at`, which needs `args.time` resolved to an exact moment).

This tool never reads or changes the customer's own appointments — use
`appointment_task` for that. Do not call this together with `finish`/
`ask_customer` in the same round — ending the turn always happens alone."""

DECIDE_PENDING_DESCRIPTION = """Resolve the one pending proposal for this turn, if any.

Call this FIRST, before any other tool, whenever a proposal is pending for
this customer — the pending proposal must be decided before anything else
can move forward (a new `appointment_task` call while one is pending gets
rejected). Usable once per turn; it always targets the proposal that was
pending when this turn started, never a proposal created earlier in this
same turn.

`label` is your read of the customer's reply after any structural veto has
already been applied for you: "approve", "reject", "change" (they want a
different time — expect a following `appointment_task` call), "unclear", or
"none" (they did not address it at all, e.g. a greeting or an aside).
`aside`, when the customer set the proposal aside instead of answering,
downgrades `label`: "relative_free" ("whenever is free" is too vague to
count as approval), "defer_only" or "defer_with_request" (they will decide
later). `reason` is your own note, quoting the customer's words that justify
the label, kept for logging — it is never shown to the customer."""


def make_orchestrator_tools(deps: OrchestratorDeps) -> list[BaseTool]:
    """Build the 5 control tools bound to one turn's `deps`.

    Built fresh per turn because the `appointment_id`/`queue_id` enums of
    `appointment_task.args` are scoped to THIS customer's current upcoming
    appointments/queued requests (§12.17) — a stale list could let the model
    target an appointment that is no longer this customer's.
    """
    appointment_ids = [a.id for a in deps.ctx.upcoming]
    queue_ids = [q.queue_id for q in deps.ctx.queued_requests]
    appointment_args_cls = appointment_args_model(appointment_ids, queue_ids)

    appointment_task_schema = create_model(
        "appointment_taskArgs",
        action=(AppointmentAction, ...),
        args=(appointment_args_cls, ...),
    )
    shop_info_task_schema = create_model(
        "shop_info_taskArgs",
        action=(ShopAction, ...),
        args=(ShopArgs, ...),
    )

    async def _appointment_task(action, args) -> str:
        spec = TaskSpec(
            task_id=f"t{next(deps.task_counter)}",
            agent="appointment",
            action=action,
            args=args.model_dump(),
        )
        result = await deps.run_appointment(spec, deps.ctx, deps.question)
        if isinstance(result.data, ProposalCreated):
            # §5.3 (C1): ghi ngay trong vòng, trước khi trả JSON cho model —
            # một lịch đã approve sau đó không bao giờ mất dù lượt bị huỷ.
            # `round`/`index` thật là việc của `OrchestratorGuard` (task
            # sau, nó ghi lại `calls` của state mỗi vòng); ở đây chỉ cần một
            # dòng ledger tồn tại sớm, nên dùng số thứ tự task làm `index`
            # và `round=0` làm giá trị trung tính, không bịa một con số vòng
            # không có nghĩa.
            await deps.ledger.append_call(
                deps.turn_id,
                CallRecord(
                    round=0,
                    index=int(spec.task_id[1:]),
                    tool="appointment_task",
                    action=action,
                    args_norm=args.model_dump_json(),
                    allowed=True,
                    result=result,
                    at=now_utc(),
                ),
            )
        return result.model_dump_json()

    appointment_task = StructuredTool.from_function(
        coroutine=_appointment_task,
        name="appointment_task",
        description=APPOINTMENT_TASK_DESCRIPTION,
        args_schema=appointment_task_schema,
    )

    async def _shop_info_task(action, args) -> str:
        spec = TaskSpec(
            task_id=f"t{next(deps.task_counter)}",
            agent="shop_info",
            action=action,
            args=args.model_dump(),
        )
        result = await deps.run_shop(spec, deps.ctx, deps.question)
        return result.model_dump_json()

    shop_info_task = StructuredTool.from_function(
        coroutine=_shop_info_task,
        name="shop_info_task",
        description=SHOP_INFO_TASK_DESCRIPTION,
        args_schema=shop_info_task_schema,
    )

    @tool(description=DECIDE_PENDING_DESCRIPTION)
    async def decide_pending(label: DecideLabel, aside: Aside | None, reason: str) -> str:
        outcome = await apply_decision(
            deps.db, deps.user, deps.ctx, label=label, aside=aside, reason=reason
        )
        return outcome.model_dump_json()

    @tool(return_direct=True)
    async def ask_customer(field: QuestionField, hint: str | None) -> str:
        """End the turn by asking the customer one clarifying question.

        Call this alone — never alongside `appointment_task`, `shop_info_task`
        or `decide_pending` in the same round; asking a question always ends
        the turn immediately. `field` names what is missing ("day", "time",
        "part_of_day", "service", "which_appointment", "week", "confirm");
        `hint`, if given, is a short note for why you are asking, not a
        sentence to read verbatim."""
        return json.dumps({"ok": True})

    @tool(return_direct=True)
    async def finish(
        kind_hint: Literal["answer", "unsupported", "social"],
        service: str | None,
        stated: list[str],
    ) -> str:
        """End the turn: everything needed to answer the customer has been gathered.

        Call this alone — never alongside `appointment_task`, `shop_info_task`
        or `decide_pending` in the same round. `kind_hint` is your best guess
        at the shape of the final reply ("answer": you have what is needed;
        "unsupported": the customer asked for something this salon does not
        do; "social": pure small talk, nothing to look up). `service`, when
        known, is the service the customer is asking about. `stated` lists,
        at most two, short facts the customer already told you this turn
        (e.g. a service name) worth remembering for later turns."""
        return json.dumps({"ok": True})

    return [appointment_task, shop_info_task, decide_pending, ask_customer, finish]
