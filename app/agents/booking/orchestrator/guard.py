"""`OrchestratorGuard` — lớp CODE quyết định mỗi tool call của Orchestrator có
được chạy hay không (§9.3 luật (a)–(k)), cộng marker/trùng/ép dừng (§5.6) và
trần vòng (§7.1).

Cơ chế thư viện (đã kiểm trên nguồn LangChain/LangGraph, xem báo cáo Task 3.3):
- Từ chối = trả `ToolMessage(status="error")` mà KHÔNG gọi `handler` — tool
  không bao giờ chạy.
- Ghi state = trả `Command(update=...)` từ `awrap_tool_call`. Các call cùng
  vòng là các task `Send` riêng trong CÙNG một superstep, đọc CÙNG một
  snapshot state — nên luật cần nhìn "anh em" đọc `tool_calls` của AIMessage
  cuối (`siblings`), không đọc bộ đếm; và trường state nào nhiều call cùng
  vòng có thể ghi đều có reducer (`state.py`).
- Ép dừng = `abefore_model` + `@hook_config(can_jump_to=["end"])` trả
  `{"jump_to": "end"}`.

Guard là người ghi DUY NHẤT `CallRecord` vào `turn_ledger` (cả call bị từ
chối lẫn call được chạy), `await` xong TRƯỚC khi trả ToolMessage cho model
(§5.3/C1). Guard không bao giờ ghi lịch: đường ghi duy nhất là
`decide_pending` → `decide.apply_decision` (`tests/test_write_fence.py`).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, ValidationError, create_model

from app.agents.booking.orchestrator.decide import DecideRejected, apply_decision, veto
from app.agents.booking.orchestrator.state import STREAK_RESET, OrchestratorState
from app.agents.booking.orchestrator.tools import OrchestratorDeps
from app.agents.booking.schemas import (
    AppointmentAction,
    AppointmentArgs,
    AppointmentList,
    AskCustomerArgs,
    CallRecord,
    DecideArgs,
    FinishArgs,
    FreeSlots,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopAction,
    ShopArgs,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
    TimeHint,
)
from app.core.clock import now_utc
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Hằng số
# ---------------------------------------------------------------------------

TASK_TOOLS = frozenset({"appointment_task", "shop_info_task"})
ENDING_TOOLS = frozenset({"finish", "ask_customer"})
TOOL_NAMES = TASK_TOOLS | ENDING_TOOLS | {"decide_pending"}
PROPOSE_ACTIONS = frozenset({"propose_book", "propose_move", "propose_cancel"})

# Mã từ chối làm `no_progress_streak` tăng ngay tại call (§5.6, brief Task 3.3).
# Vòng toàn bị từ chối bằng mã KHÁC được `abefore_model` cộng 1 thay.
STREAK_CODES = frozenset({"duplicate_call", "must_decide_pending"})

# (h): sau ngần này lần LIÊN TIẾP bị `must_decide_pending`, code tự ghi `kept`.
MUST_DECIDE_LIMIT = 2

# (b): khoảng hợp lệ của TimeHint — schema strict-safe không mang
# minimum/maximum (§12.17), nên kiểm ở đây.
_RANGES = {"hour": (0, 23), "minute": (0, 59), "weekday": (0, 6), "day_offset": (-1, 90)}
MAX_STATED = 2

# Trường chỉ để log/giải thích, không thuộc "khoá" của call (và có thể chứa
# chữ khách do model trích) — bỏ khỏi `args_norm`.
_DROP_KEYS = frozenset({"reason", "hint"})

REJECT_HINTS: dict[str, str] = {
    "unknown_tool": (
        "This tool does not exist. Use only appointment_task, shop_info_task, decide_pending, "
        "ask_customer or finish."
    ),
    "bad_args": (
        "The arguments do not match the tool schema or are out of range (hour 0-23, minute 0-59, "
        "weekday 0-6, day_offset -1 to 90, at most 2 items in stated). Fix them and call again."
    ),
    "unknown_appointment_id": (
        "appointment_id must be one of the ids of the customer's upcoming appointments shown in the "
        "context. Copy an exact id from there."
    ),
    "unknown_queue_id": "queue_id must be one of the queued request ids shown in the context.",
    "missing_anchor_target": (
        "The time anchor points to nothing: there is no pending proposal, recently declined proposal, "
        "upcoming appointment or queue_id for it. Set anchor to null or use an anchor that exists in "
        "the context."
    ),
    "one_proposal_per_turn": (
        "Only one proposal can be made per turn and one already exists. Put extra requests in `also` "
        "of the single propose_book call, or finish and tell the customer."
    ),
    "duplicate_call": (
        "You already made this exact call and nothing new was learned since. Use the earlier result, "
        "change the arguments, or finish."
    ),
    "serialize_required": (
        "finish and ask_customer must be the only call in their round, and only one of propose_*/"
        "decide_pending may run per round. Call it again on its own in the next round."
    ),
    "must_decide_pending": (
        "A proposal is still pending. Call decide_pending first (label none if the customer did not "
        "address it), then continue."
    ),
    "same_time_as_pending": (
        "The customer restated the exact time of the pending proposal, so this is not a change. If "
        "they only confirm it, call decide_pending with label approve. If they want a different "
        "service at that same time, call decide_pending with label reject, then propose again."
    ),
    "already_approved": (
        "The pending proposal was approved this turn. Do not propose again; finish and tell the "
        "customer it is done."
    ),
    "already_decided": (
        "decide_pending already ran this turn and can be used only once. Continue with the outcome "
        "you already have."
    ),
    "too_many_calls": "Too many calls in one round. Make this call again in the next round.",
    "no_pending": (
        "There is no pending proposal for this customer, so there is nothing to decide. Do not call "
        "decide_pending this turn."
    ),
}
_FALLBACK_HINT = "This call was rejected. Re-read the context, then make a different call or finish."

# (b): model parse tham số — bản TĨNH (id là `str | None`); việc id có thuộc
# khách này không là luật (c), có mã riêng.
_ARG_MODELS: dict[str, type[BaseModel]] = {
    "appointment_task": create_model(
        "AppointmentTaskCall", action=(AppointmentAction, ...), args=(AppointmentArgs, ...)
    ),
    "shop_info_task": create_model("ShopInfoTaskCall", action=(ShopAction, ...), args=(ShopArgs, ...)),
    "decide_pending": DecideArgs,
    "ask_customer": AskCustomerArgs,
    "finish": FinishArgs,
}


# ---------------------------------------------------------------------------
# Hàm thuần
# ---------------------------------------------------------------------------


def _drop_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_keys(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, list):
        return [_drop_keys(v) for v in value]
    return value


def normalize_args(tool: str, args: dict) -> str:
    """Khoá so trùng (§5.6) và `CallRecord.args_norm`: JSON sắp khoá, bỏ
    `reason`/`hint` ở mọi tầng (chỉ là ghi chú của model, có thể trích chữ
    khách — không được vào log)."""
    return json.dumps(_drop_keys(args or {}), sort_keys=True, ensure_ascii=False, default=str)


def is_duplicate(calls: list[dict], tool: str, action: str | None, args_norm: str) -> bool:
    """Có call ĐƯỢC CHẠY cùng khoá `(tool, action, args_norm)` trước đó mà từ
    lần đó tới giờ không call nào sinh marker mới. Call trước bị từ chối không
    tính (gọi lại đúng nó là sửa sai, không phải lặp)."""
    last = None
    for i, rec in enumerate(calls):
        if (
            rec.get("allowed")
            and rec.get("tool") == tool
            and rec.get("action") == action
            and rec.get("args_norm") == args_norm
        ):
            last = i
    if last is None:
        return False
    return not any(rec.get("markers") for rec in calls[last + 1:])


def markers_for(tool: str, action: str | None, result: TaskResult | PendingOutcome | None) -> list[str]:
    """§5.6 — marker tiến triển của MỘT kết quả. `pending_decided:<x>` dùng
    `outcome` cuối (code quyết, sau veto), không phải nhãn model gửi lên."""
    if isinstance(result, PendingOutcome):
        return [f"pending_decided:{result.outcome}"]
    if not isinstance(result, TaskResult) or result.data is None:
        return []
    data = result.data
    if isinstance(data, ResolvedTime):
        return ["time_resolved"] if data.start_at is not None else []
    if isinstance(data, FreeSlots):
        return [f"slots_found:{data.day.isoformat()}"]
    if isinstance(data, ProposalCreated):
        return [f"proposal_created:{data.proposal_id}"]
    if isinstance(data, AppointmentList):
        return ["appointments_listed"]
    if isinstance(data, ShopHoursOut):
        return ["shop_hours_known"]
    if isinstance(data, ShopStatusOut):
        return ["shop_status_known"]
    if isinstance(data, ShopOpenOut):
        return [f"shop_open_checked:{data.at.iso}"]
    return []


def siblings(messages) -> list[dict]:
    """`tool_calls` của AIMessage cuối — các call cùng vòng với call đang xét."""
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            return list(msg.tool_calls or [])
    return []


def is_propose(tool: str, args: dict) -> bool:
    return tool == "appointment_task" and isinstance(args, dict) and args.get("action") in PROPOSE_ACTIONS


def _is_sequential(call: dict) -> bool:
    """Nhóm tuần tự (g): `propose_*`, `decide_pending`, `ask_customer`, `finish`."""
    name = call.get("name")
    return name in ENDING_TOOLS or name == "decide_pending" or is_propose(name, call.get("args") or {})


def _index_of(call: dict, sibs: list[dict]) -> int:
    for i, s in enumerate(sibs):
        if s.get("id") == call.get("id"):
            return i
    return 0


def rule_g(call: dict, sibs: list[dict]) -> str | None:
    """(g) nhóm tuần tự — cách đọc D′ (controller chốt sau Task 3.3):

    Chỉ áp khi AIMessage cuối có >1 call. Khi đó:
    - `finish`/`ask_customer` LUÔN bị `serialize_required` (kết thúc lượt
      phải đi một mình — `return_direct` chỉ thoát khi MỌI tool vừa chạy là
      return_direct, §5.3);
    - `propose_*`/`decide_pending` chỉ chạy nếu KHÔNG có anh em index NHỎ HƠN
      thuộc nhóm tuần tự; có → `serialize_required` (spec §13: hai
      `propose_book` cùng AIMessage → call index 1 bị từ chối, DB một đề xuất;
      `decide_pending` trước rồi mới `propose_*` ở vòng sau);
    - anh em NGOÀI nhóm (`resolve_time`, `find_slots`, `list_appointments`,
      `shop_info_task`) không bao giờ chặn, và chính chúng không bị (g).
    """
    if len(sibs) <= 1 or not _is_sequential(call):
        return None
    if call.get("name") in ENDING_TOOLS:
        return "serialize_required"
    index = _index_of(call, sibs)
    if any(_is_sequential(s) for s in sibs[:index]):
        return "serialize_required"
    return None


def rule_e(call: dict, sibs: list[dict], proposals_created: int) -> str | None:
    """(e) một đề xuất/lượt: `propose_*` bị chặn nếu lượt đã có đề xuất HOẶC
    có anh em `propose_*` index nhỏ hơn. Mệnh đề anh em là phòng thủ chiều
    sâu: trong `awrap_tool_call` (g) chạy trước và đã chặn trường hợp đó."""
    if not is_propose(call.get("name"), call.get("args") or {}):
        return None
    if proposals_created >= 1:
        return "one_proposal_per_turn"
    index = _index_of(call, sibs)
    if any(is_propose(s.get("name"), s.get("args") or {}) for s in sibs[:index]):
        return "one_proposal_per_turn"
    return None


def reject(call: dict, code: str) -> ToolMessage:
    """Từ chối = ToolMessage lỗi, không exception (§9.3)."""
    return ToolMessage(
        content=json.dumps({"error_code": code, "hint": REJECT_HINTS.get(code, _FALLBACK_HINT)}),
        tool_call_id=call["id"],
        name=call["name"],
        status="error",
    )


def _time_hints(tool: str, parsed: BaseModel) -> list[TimeHint]:
    if tool not in TASK_TOOLS:
        return []
    args = parsed.args
    hints = [args.time] if args.time is not None else []
    for extra in getattr(args, "also", None) or []:
        hints.append(extra.time)
    return hints


def _out_of_range(tool: str, parsed: BaseModel) -> bool:
    for hint in _time_hints(tool, parsed):
        for name, (low, high) in _RANGES.items():
            value = getattr(hint, name)
            if value is not None and not low <= value <= high:
                return True
    return tool == "finish" and len(parsed.stated) > MAX_STATED


def _round_number(messages) -> int:
    """Số thứ tự (từ 1) của vòng hiện tại = số AIMessage có tool call."""
    return max(1, sum(1 for m in messages or [] if isinstance(m, AIMessage) and m.tool_calls))


def _last_round_tool_messages(messages) -> list[ToolMessage]:
    out: list[ToolMessage] = []
    for msg in reversed(messages or []):
        if isinstance(msg, ToolMessage):
            out.append(msg)
        elif isinstance(msg, AIMessage):
            return out
    return []


def _error_code_of(msg: ToolMessage) -> str | None:
    try:
        return json.loads(msg.content).get("error_code")
    except (TypeError, ValueError, AttributeError):
        return None


def _consecutive_must_decide(calls: list[dict]) -> int:
    """Số lần `must_decide_pending` liên tiếp gần nhất; một call ĐƯỢC CHẠY cắt chuỗi."""
    count = 0
    for rec in reversed(calls):
        if rec.get("allowed"):
            break
        if rec.get("reject_code") == "must_decide_pending":
            count += 1
    return count


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class OrchestratorGuard(AgentMiddleware):
    state_schema = OrchestratorState

    def __init__(self, deps: OrchestratorDeps, *, max_calls_per_round: int, no_progress_limit: int):
        super().__init__()
        self.deps = deps
        self.max_calls_per_round = max_calls_per_round
        self.no_progress_limit = no_progress_limit

    # -- log -----------------------------------------------------------------

    def _base_extra(self, request) -> dict:
        user = self.deps.user
        user_id = str(user.id) if user is not None else self.deps.ctx.customer.id
        config = getattr(getattr(request, "runtime", None), "config", None) or {}
        thread_id = (config.get("configurable") or {}).get("thread_id")
        return {"user_id": user_id, "turn_id": self.deps.turn_id, "thread_id": thread_id}

    def _log_pending_decided(
        self, request, *, label: str | None, aside: str | None, outcome: PendingOutcome, veto_reason: str | None
    ) -> None:
        """§11 `pending_decided` — KHÔNG log `reason` (cụm chữ khách model trích)."""
        label_final = label
        pending = self.deps.ctx.pending
        if label is not None and pending is not None:
            queued = [q.start_at for q in self.deps.ctx.queued_requests]
            v = veto(label, aside, self.deps.question, pending, self.deps.ctx.asked_at, queued=queued)
            label_final, veto_reason = v.label, v.reason
        logger.info(
            "pending_decided",
            extra={
                **self._base_extra(request),
                "proposal_id": outcome.proposal_id,
                "label_from_model": label,
                "aside": aside,
                "label_final": label_final,
                "veto_reason": veto_reason,
                "outcome": outcome.outcome,
            },
        )

    async def _record(self, request, record: CallRecord) -> dict:
        """Ghi ledger (await, trước khi ToolMessage về model) + log `orch_call`."""
        await self.deps.ledger.append_call(self.deps.turn_id, record)
        logger.info(
            "orch_call",
            extra={
                **self._base_extra(request),
                "round": record.round,
                "call_index": record.index,
                "tool": record.tool,
                "action": record.action,
                "args_norm": record.args_norm,
                "allowed": record.allowed,
                "reject_code": record.reject_code,
            },
        )
        return record.model_dump(mode="json")

    # -- luật ------------------------------------------------------------------

    def _check(self, call: dict, state: dict, sibs: list[dict], index: int, action, args_norm) -> tuple[str | None, Any]:
        """Thứ tự kiểm đúng brief: (a) (b) too_many (c) (d) (g) (h) (k) (j) (e) (f)."""
        tool = call["name"]
        raw = call.get("args") or {}
        ctx = self.deps.ctx

        if tool not in TOOL_NAMES:  # (a)
            return "unknown_tool", None
        try:  # (b)
            parsed = _ARG_MODELS[tool].model_validate(raw)
        except ValidationError:
            return "bad_args", None
        if _out_of_range(tool, parsed):
            return "bad_args", parsed
        if index >= self.max_calls_per_round:
            return "too_many_calls", parsed

        hints = _time_hints(tool, parsed)
        if tool == "appointment_task":  # (c)
            appointment_id = parsed.args.appointment_id
            if appointment_id is not None and appointment_id not in {a.id for a in ctx.upcoming}:
                return "unknown_appointment_id", parsed
        queue_ids = {q.queue_id for q in ctx.queued_requests}
        if any(h.queue_id is not None and h.queue_id not in queue_ids for h in hints):
            return "unknown_queue_id", parsed
        for h in hints:  # (d)
            if (
                (h.anchor == "pending" and ctx.pending is None)
                or (h.anchor == "declined" and ctx.recently_declined is None)
                or (h.anchor == "appointment" and not ctx.upcoming)
                or (h.anchor == "queued" and h.queue_id is None)
            ):
                return "missing_anchor_target", parsed

        if code := rule_g(call, sibs):  # (g)
            return code, parsed
        propose = is_propose(tool, raw)
        if (  # (h)
            ctx.pending is not None
            and not state.get("pending_decided")
            and (propose or tool in ENDING_TOOLS)
        ):
            return "must_decide_pending", parsed
        if tool == "decide_pending" and state.get("pending_decided"):  # (k)
            return "already_decided", parsed
        outcome = state.get("pending_outcome") or {}
        if propose and outcome.get("outcome") == "approved":  # (j)
            return "already_approved", parsed
        if code := rule_e(call, sibs, state.get("proposals_created") or 0):  # (e)
            return code, parsed
        if is_duplicate(state.get("calls") or [], tool, action, args_norm):  # (f)
            return "duplicate_call", parsed
        return None, parsed

    # -- hook ------------------------------------------------------------------

    async def awrap_tool_call(
        self, request, handler: Callable[[Any], Awaitable[ToolMessage | Command]]
    ) -> Command:
        call = request.tool_call
        state = request.state or {}
        messages = state.get("messages") or []
        sibs = siblings(messages)
        index = _index_of(call, sibs)
        tool = call["name"]
        raw = call.get("args") or {}
        action = raw.get("action") if tool in TASK_TOOLS and isinstance(raw.get("action"), str) else None
        args_norm = normalize_args(tool, raw)
        base = dict(round=_round_number(messages), index=index, tool=tool, action=action, args_norm=args_norm)

        code, parsed = self._check(call, state, sibs, index, action, args_norm)
        if code is not None:
            return await self._rejected(request, state, base, code)

        try:
            msg = await handler(request)
        except DecideRejected as exc:
            return await self._rejected(request, state, base, exc.reject_code)
        return await self._allowed(request, state, base, parsed, msg)

    async def _rejected(self, request, state: dict, base: dict, code: str) -> Command:
        call = request.tool_call
        update: dict[str, Any] = {"messages": [reject(call, code)]}
        if code in STREAK_CODES:
            update["no_progress_streak"] = 1
            if (state.get("no_progress_streak") or 0) + 1 >= self.no_progress_limit:
                update["force_end"] = True
        if code == "must_decide_pending":
            consecutive = _consecutive_must_decide(state.get("calls") or []) + 1
            update["must_decide_rejections"] = consecutive
            if consecutive >= MUST_DECIDE_LIMIT:
                # (h): model bỏ qua decide_pending 2 lần liền → code tự giữ đề
                # xuất (`kept`, không đụng DB) để lượt không kẹt.
                outcome = await apply_decision(self.deps, "none")
                update["pending_decided"] = True
                update["pending_outcome"] = outcome.model_dump(mode="json")
                self._log_pending_decided(
                    request, label=None, aside=None, outcome=outcome, veto_reason="must_decide_twice"
                )
        record = CallRecord(**base, allowed=False, reject_code=code, at=now_utc())
        update["calls"] = [await self._record(request, record)]
        return Command(update=update)

    async def _allowed(self, request, state: dict, base: dict, parsed: BaseModel, msg) -> Command:
        tool = base["tool"]
        result = _parse_result(tool, msg)
        known = set(state.get("progress_markers") or [])
        new_markers = [m for m in markers_for(tool, base["action"], result) if m not in known]

        update: dict[str, Any] = {"messages": [msg]}
        if new_markers:
            update["progress_markers"] = new_markers
            update["no_progress_streak"] = STREAK_RESET
        if isinstance(result, TaskResult) and isinstance(result.data, ProposalCreated):
            update["proposals_created"] = 1
        if tool == "decide_pending" and isinstance(result, PendingOutcome):
            update["pending_decided"] = True
            update["pending_outcome"] = result.model_dump(mode="json")
            if result.outcome == "clarify":  # unclear lần 1 → ép dừng (§5.5)
                update["force_end"] = True
            self._log_pending_decided(
                request, label=parsed.label, aside=parsed.aside, outcome=result, veto_reason=None
            )
        if tool == "finish":
            update["ended_by"] = "finish"
            update["finish_args"] = parsed.model_dump(mode="json")
        elif tool == "ask_customer":
            update["ended_by"] = "ask_customer"
            update["ask_args"] = parsed.model_dump(mode="json")

        record = CallRecord(**base, allowed=True, result=result, markers=new_markers, at=now_utc())
        update["calls"] = [await self._record(request, record)]
        return Command(update=update)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:
        """Trước mỗi lần gọi model: ép dừng (§5.6) hoặc dừng vì trần vòng (§7.1).

        Vòng vừa rồi có ≥1 call chạy được (ToolMessage không lỗi) → tính vào
        `rounds_executed`. Vòng TOÀN bị từ chối không tính trần nhưng cộng 1
        vào `no_progress_streak` — trừ khi một từ chối trong vòng đó đã tự
        cộng (`STREAK_CODES`) — để một vòng `serialize_required` lặp lại đi
        tới `forced` thay vì lặp tới `recursion_limit`.

        `finish`/`ask_customer` không `return_direct` (ruling Task 3.3): khi
        guard đã CHO PHÉP một trong hai, nó ghi `ended_by` — ở đây nhảy END và
        giữ nguyên `ended_by` đó (kiểm trước `force_end` để không đè thành
        "forced").
        """
        if state.get("ended_by") in ENDING_TOOLS:
            return {"jump_to": "end"}
        if state.get("force_end"):
            return {"jump_to": "end", "ended_by": "forced"}

        update: dict[str, Any] = {}
        streak = state.get("no_progress_streak") or 0
        rounds = state.get("rounds_executed") or 0
        tool_messages = _last_round_tool_messages(state.get("messages"))
        if tool_messages:
            if all(m.status == "error" for m in tool_messages):
                if not any(_error_code_of(m) in STREAK_CODES for m in tool_messages):
                    update["no_progress_streak"] = 1
                    streak += 1
            else:
                rounds += 1
                update["rounds_executed"] = rounds

        if streak >= self.no_progress_limit:
            return {**update, "jump_to": "end", "ended_by": "forced"}
        if rounds >= self.deps.max_rounds:
            return {**update, "jump_to": "end", "ended_by": "limit"}
        return update or None


def _parse_result(tool: str, msg) -> TaskResult | PendingOutcome | None:
    if not isinstance(msg, ToolMessage) or msg.status == "error":
        return None
    model: type[BaseModel] | None = (
        TaskResult if tool in TASK_TOOLS else PendingOutcome if tool == "decide_pending" else None
    )
    if model is None:
        return None
    try:
        return model.model_validate_json(msg.content)
    except (ValidationError, TypeError):
        return None
