"""Vỏ LangGraph MỘT lượt khách của gói mới (Task 5.3, §5.3 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

```
START → resolve ──R1──→ button_path ──┐
          │ R2/R3/R5                   ↓
          └──→ orchestrate ────────→ brief → respond → record → END
```

Không còn `interrupt`/`Command(resume=...)`: mỗi lượt chạy từ START, đề xuất
đang chờ được `resolve` đọc lại từ DB (`TurnContext.pending`). Thread
checkpointer chỉ giữ câu khách (events.py đưa vào input) và câu đáp cuối
(`record` nối thêm) — tool exchange của Orchestrator/specialist sống trong
agent con (`checkpointer=False`), không bao giờ vào thread.

Graph không tự ghi lịch: đường ghi duy nhất là `apply_decision` (qua
`decide_pending` của Orchestrator hoặc `run_button`) — `tests/test_write_fence.py`.
Mọi khoá của lượt được đặt lại ở `resolve` (thread giữ state lượt trước,
khoá không có trong input sẽ mang giá trị cũ sang).

Log §11 do graph phát (`resolve_done`, `unannounced_reported`, `goal_progress`,
`turn_ended`) luôn có `user_id`/`turn_id`/`thread_id` trong `extra`, không bao
giờ chứa chữ khách.
"""
from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing_extensions import TypedDict

from app.agents.booking import conversation
from app.agents.booking.button_path import run_button
from app.agents.booking.context import TurnContext, load_turn_context
from app.agents.booking.guard import apply_guard, normalize
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.llm_budget import BudgetMiddleware, LlmBudget, count_model_call
from app.agents.booking.orchestrator.agent import OrchestrateOutcome, run_orchestrate
from app.agents.booking.orchestrator.tools import OrchestratorDeps
from app.agents.booking.progress import step_goal
from app.agents.booking.reply_brief import (
    ButtonOutcome,
    TurnOutcome,
    build_reply_brief,
)
from app.agents.booking.resolver import ResolveResult
from app.agents.booking.resolver import resolve as resolve_route
from app.agents.booking.schemas import Fact, ReplyBrief
from app.agents.booking.specialists import appointment, shop_info
from app.agents.booking.button import ButtonReply
from app.agents.booking.history import model_history, stamp
from app.agents.checkpoint import get_checkpointer
from app.core.clock import now_ms
from app.core.config import settings
from app.core.logging import get_logger
from app.models.conversation import GoalProgress
from app.models.user import User
from app.services.conversation_state import ConversationStateService

logger = get_logger(__name__)

# §7.3: vượt trần LLM/giờ thì lượt vẫn chạy nhưng Orchestrator chỉ còn 2 vòng.
OVER_BUDGET_MAX_ROUNDS = 2

# Orchestrator chỉ đọc 2 cặp (khách, trợ lý) gần nhất (§8.4).
RECENT_MESSAGES = 4

_ALL_CLAIMS = ["booked", "cancelled", "moved"]

# Lượt nút bấm không có `ended_by` của Orchestrator — kết thúc bình thường.
BUTTON_ENDED_BY = "finish"

# Nút Đồng ý/Không đã quyết dứt khoát — đóng mục tiêu (§7.2), như
# `approved`/`rejected` của `decide_pending` trong `step_goal`.
_GOAL_CLOSING_BUTTON_KINDS = frozenset({"approved", "rejected"})

# §7.2: chạm ngưỡng mục tiêu → mời khách gọi tiệm. Câu bắt buộc + câu chốt.
LIMIT_PHONE_LINE = "{address} gọi giúp em số {phone} nhé."
LIMIT_NO_PHONE_LINE = "{address} gọi điện cho tiệm giúp em nhé."


class GraphState(TypedDict, total=False):
    """State một lượt. `messages` (thread) chỉ chứa Human/AI của câu khách và
    câu đáp; mọi khoá khác là của MỘT lượt, `resolve` đặt lại đầu mỗi lượt."""

    messages: Annotated[list[AnyMessage], add_messages]
    turn_id: str
    asked_at: datetime
    history_after: datetime | None
    customer_text: str
    button: dict | None          # ButtonReply dump (events.py); None = gõ chữ
    turn_context: dict              # TurnContext.model_dump(mode="json")
    resolve: dict                   # ResolveResult dump
    outcome: dict | None         # OrchestrateOutcome | ButtonOutcome dump (+ "type")
    brief: dict | None
    draft: str
    answer: str
    answer_source: str
    violations: list[str]
    previous_replies: list[str]
    reply_kind: str | None
    ended_by: str | None
    goal_limit_hit: bool
    goal: dict | None            # GoalProgress sau `step_goal` — `record` ghi
    # Lượt TRƯỚC có kết quả vừa được báo trong câu đáp này (R0) — events.py
    # `mark_announced` lượt đó. None khi câu đáp cuối không chứa dòng R0.
    announced_turn_id: str | None


# Khoá của MỘT lượt — đặt lại ở `resolve` (thread giữ giá trị lượt trước).
_TURN_RESET = {
    "outcome": None,
    "brief": None,
    "draft": "",
    "answer": "",
    "answer_source": "llm",
    "violations": [],
    "reply_kind": None,
    "ended_by": None,
    "goal_limit_hit": False,
    "goal": None,
    "announced_turn_id": None,
}


def _thread_id(config: RunnableConfig | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("thread_id")


def _ids(user: User, state: GraphState, config: RunnableConfig | None) -> dict:
    """Ba khoá `extra` bắt buộc của mọi log §11."""
    return {"user_id": str(user.id), "turn_id": state.get("turn_id"), "thread_id": _thread_id(config)}


def _ctx(state: GraphState) -> TurnContext:
    return TurnContext.model_validate(state["turn_context"])


def _outcome(raw: dict | None) -> TurnOutcome | None:
    if not raw:
        return None
    data = {k: v for k, v in raw.items() if k != "type"}
    if raw.get("type") == "button":
        return ButtonOutcome.model_validate(data)
    return OrchestrateOutcome.model_validate(data)


def _history(state: GraphState) -> list[AnyMessage]:
    """Cửa sổ lịch sử (không gồm câu khách lượt này — `model_history` bỏ câu
    khách chưa có câu đáp ngay sau)."""
    return model_history(
        state.get("messages") or [], after=state.get("history_after"), now=state["asked_at"]
    )


def limit_line(address: str) -> str:
    """Câu mời gọi tiệm (§7.2) — có số thì đọc số, không có thì nói chung."""
    spoken_address = address[:1].upper() + address[1:]
    if settings.shop_phone:
        return LIMIT_PHONE_LINE.format(address=spoken_address, phone=settings.shop_phone)
    return LIMIT_NO_PHONE_LINE.format(address=spoken_address)


def _limit_reached(brief: ReplyBrief) -> ReplyBrief:
    """§7.2: chạm ngưỡng `GoalProgress` → mời gọi tiệm. Câu mời là `must_say`
    VÀ `fallback`, để guard bắt buộc nó. Lượt không có tiến triển nào nên cấm
    mọi claim (như dòng `limit` §5.4); bỏ `ask` — hỏi lại đúng câu đang bế
    tắc là thứ ngưỡng này sinh ra để chặn."""
    claims = list(brief.must_not_claim)
    claims.extend(c for c in _ALL_CLAIMS if c not in claims)
    line = limit_line(brief.address)
    return brief.model_copy(update={
        "kind": "limit_reached",
        "facts": [*brief.facts, Fact(key="shop_phone", text=settings.shop_phone)],
        "must_say": [*brief.must_say, line],
        "must_not_claim": claims,
        "ask": None,
        "fallback": line,
    })


def _turn_ended_extra(outcome: TurnOutcome | None, ended_by: str | None) -> dict:
    """Trường `turn_ended` (§11) — đọc từ `OrchestrateOutcome.calls`; lượt nút
    bấm không có vòng tool nào."""
    if not isinstance(outcome, OrchestrateOutcome):
        return {
            "ended_by": ended_by, "rounds_counted": 0, "rounds_rejected_only": 0,
            "calls_n": 0, "rejects_n": 0, "markers": [], "no_progress_streak": 0,
        }
    rounds: dict[int, bool] = {}
    for call in outcome.calls:
        rounds[call.round] = rounds.get(call.round, False) or call.allowed
    return {
        "ended_by": ended_by,
        "rounds_counted": outcome.rounds_executed,
        "rounds_rejected_only": sum(1 for any_allowed in rounds.values() if not any_allowed),
        "calls_n": len(outcome.calls),
        "rejects_n": sum(1 for c in outcome.calls if not c.allowed),
        "markers": [m for c in outcome.calls for m in c.markers],
        "no_progress_streak": outcome.no_progress_streak,
    }


def build_graph(db: AsyncIOMotorDatabase, user: User, checkpointer=None):
    """Đồ thị cho một khách cụ thể, dựng lại mỗi lượt (tool đóng `user`
    trong closure — user_id không bao giờ là tham số model điều khiển được).

    `checkpointer=None` → `get_checkpointer()` lúc dựng (không phải tham số
    mặc định: chạy lúc import sẽ mở MongoClient vào DB dev cả trong test).
    Agent con (Orchestrator, specialist) luôn `checkpointer=False`."""
    user_id = str(user.id)

    async def resolve(state: GraphState, config: RunnableConfig) -> dict:
        turn_id = state["turn_id"]
        asked_at = state["asked_at"]
        # Ghi chú controller 1: nạp bối cảnh TRƯỚC `TurnLedger.start` — start
        # trước sẽ biến chính lượt này thành previous_turns[0].
        ctx = await load_turn_context(db, user, turn_id=turn_id, asked_at=asked_at)
        button = state.get("button")
        question = ButtonReply(**button) if button else (state.get("customer_text") or "")
        result = resolve_route(ctx, question)
        ctx = ctx.model_copy(update={"answers_open_question_hint": result.answers_open_question_hint})
        await TurnLedger(db).start(
            turn_id=turn_id, user_id=user_id, route=result.route, rule=result.rule, asked_at=asked_at,
        )
        logger.info("resolve_done", extra={
            **_ids(user, state, config),
            "rule": result.rule,
            "has_pending": ctx.pending is not None,
            "has_open_question": ctx.open_question is not None,
            "queued_n": len(ctx.queued_requests),
            "has_unannounced": ctx.unannounced is not None,
        })
        return {
            **_TURN_RESET,
            "turn_context": ctx.model_dump(mode="json"),
            "resolve": result.model_dump(mode="json"),
        }

    def _deps(state: GraphState, ctx: TurnContext, *, max_rounds: int, **kw) -> OrchestratorDeps:
        return OrchestratorDeps(
            db=db, user=user, ctx=ctx, ledger=TurnLedger(db), turn_id=state["turn_id"],
            question=state.get("customer_text") or "", max_rounds=max_rounds, **kw,
        )

    async def button_path(state: GraphState) -> dict:
        ctx = _ctx(state)
        # Không chạy Orchestrator: `max_rounds`/`run_*` không được dùng tới.
        deps = _deps(state, ctx, max_rounds=settings.orch_max_tool_rounds)
        outcome = await run_button(deps, ButtonReply(**state["button"]))
        return {
            "outcome": {"type": "button", **outcome.model_dump(mode="json")},
            "ended_by": BUTTON_ENDED_BY,
        }

    async def orchestrate(state: GraphState, config: RunnableConfig) -> dict:
        ctx = _ctx(state)
        budget = LlmBudget(db, user_id)
        # Ghi chú controller 7: đọc trần MỘT lần, trước mọi lời gọi model của lượt.
        over = await budget.over()
        max_rounds = (
            min(OVER_BUDGET_MAX_ROUNDS, settings.orch_max_tool_rounds) if over
            else settings.orch_max_tool_rounds
        )
        if over:
            logger.info("llm_budget_over", extra={**_ids(user, state, config), "max_rounds": max_rounds})
        budget_mw = [BudgetMiddleware(budget)]
        deps = _deps(
            state, ctx, max_rounds=max_rounds,
            # Tra `run_task` lúc chạy (không lúc import) để test thay được.
            run_appointment=partial(appointment.run_task, db, user, extra_middleware=budget_mw),
            run_shop=partial(shop_info.run_task, db, user, extra_middleware=budget_mw),
        )
        recent = _history(state)[-RECENT_MESSAGES:]
        outcome = await run_orchestrate(
            deps, recent, thread_id=_thread_id(config), extra_middleware=budget_mw,
        )
        return {
            "outcome": {"type": "orchestrate", **outcome.model_dump(mode="json")},
            "ended_by": outcome.ended_by,
        }

    async def brief(state: GraphState, config: RunnableConfig) -> dict:
        ctx = _ctx(state)
        rr = ResolveResult.model_validate(state["resolve"])
        outcome = _outcome(state.get("outcome"))
        previous = state.get("previous_replies") or []
        reply = build_reply_brief(
            ctx, outcome, previous_reply=previous[-1] if previous else None, now=ctx.now_local,
        )
        orchestrated = outcome if isinstance(outcome, OrchestrateOutcome) else None
        goal, limit_hit = step_goal(ctx.goal, orchestrated, rr, ctx)
        if isinstance(outcome, ButtonOutcome) and outcome.kind in _GOAL_CLOSING_BUTTON_KINDS:
            # `step_goal` chỉ đọc OrchestrateOutcome — nút quyết dứt khoát
            # cũng đóng mục tiêu (fix round 1, Task 5.3).
            goal, limit_hit = GoalProgress(), False
        if limit_hit:
            reply = _limit_reached(reply)
        if ctx.unannounced is not None:
            logger.info("unannounced_reported", extra={
                **_ids(user, state, config),
                "proposal_id": ctx.unannounced.proposal_id,
                "outcome": ctx.unannounced.outcome,
            })
        return {
            "brief": reply.model_dump(mode="json"),
            "reply_kind": reply.kind,
            "goal": goal.model_dump(mode="json"),
            "goal_limit_hit": limit_hit,
        }

    async def respond(state: GraphState, config: RunnableConfig) -> dict:
        reply = ReplyBrief.model_validate(state["brief"])
        outcome = _outcome(state.get("outcome"))
        ids = _ids(user, state, config)
        orchestrator_failed = isinstance(outcome, OrchestrateOutcome) and outcome.ended_by == "error"
        hard_fallback = reply.kind == "stale_button" or orchestrator_failed
        if hard_fallback:
            # §5.3/§10: câu mẫu/câu cứng, không gọi model (pending coi là kept).
            draft, source = reply.fallback, "code"
        else:
            await count_model_call(LlmBudget(db, user_id))
            draft, source = await conversation.respond(
                reply, _history(state), state.get("customer_text") or "", log_extra=ids,
            )
        guarded = await apply_guard(
            draft, reply, state.get("previous_replies") or [], source=source, log_extra=ids,
        )
        # R0: chỉ coi là đã báo khi câu đáp CUỐI thật sự chứa dòng R0 — câu
        # cứng/câu chốt code hay bản nháp guard giữ lại có thể thiếu nó.
        announced_turn_id = None
        unannounced = _ctx(state).unannounced
        if (
            unannounced is not None
            and reply.announced_line
            and not hard_fallback
            and normalize(reply.announced_line) in normalize(guarded.answer)
        ):
            announced_turn_id = unannounced.turn_id
        return {
            "draft": draft,
            "answer": guarded.answer,
            "answer_source": guarded.source,
            "violations": guarded.violations,
            "announced_turn_id": announced_turn_id,
        }

    async def record(state: GraphState, config: RunnableConfig) -> dict:
        """Nối câu đáp vào thread (mốc `now_ms()` — events.py ghi
        `conversations` bằng đúng mốc này), chốt ledger, ghi `GoalProgress`.
        Mọi lệnh ghi ở đây fail-soft (ghi chú controller 2, §10): câu đáp đã
        có, mất một dòng sổ sách không được làm hỏng lượt."""
        ids = _ids(user, state, config)
        turn_id = state["turn_id"]
        outcome = _outcome(state.get("outcome"))
        ended_by = state.get("ended_by")
        reply_kind = state.get("reply_kind")
        ledger = TurnLedger(db)

        if isinstance(outcome, OrchestrateOutcome) and outcome.ask is not None:
            try:
                await ledger.set_ask(turn_id, outcome.ask.field)
            except Exception as exc:
                logger.warning("ledger_set_ask_failed", extra={**ids, "error_type": type(exc).__name__})
        try:
            await ledger.finish(turn_id, ended_by=ended_by, reply_kind=reply_kind)
        except Exception as exc:
            logger.warning("ledger_finish_failed", extra={**ids, "error_type": type(exc).__name__})

        goal = GoalProgress.model_validate(state["goal"]) if state.get("goal") else None
        if goal is not None and goal != _ctx(state).goal:
            try:
                await ConversationStateService(db).record_goal_progress(user_id, goal)
                logger.info("goal_progress", extra={
                    **ids, "goal_id": goal.goal_id, "turns_without_progress": goal.turns_without_progress,
                })
            except Exception as exc:
                logger.warning("goal_progress_persist_failed", extra={**ids, "error_type": type(exc).__name__})

        logger.info("turn_ended", extra={**ids, **_turn_ended_extra(outcome, ended_by)})

        # Nút của lượt này không được rò sang lượt gõ chữ sau (thread giữ khoá).
        fields: dict = {"button": None}
        answer = state.get("answer") or ""
        if answer:
            fields["messages"] = [stamp(AIMessage(content=answer), now_ms())]
        return fields

    graph = StateGraph(GraphState)
    graph.add_node("resolve", resolve)
    graph.add_node("button_path", button_path)
    graph.add_node("orchestrate", orchestrate)
    graph.add_node("brief", brief)
    graph.add_node("respond", respond)
    graph.add_node("record", record)

    graph.add_edge(START, "resolve")
    graph.add_conditional_edges(
        "resolve",
        lambda state: state["resolve"]["route"],
        {"button": "button_path", "orchestrate": "orchestrate"},
    )
    graph.add_edge("button_path", "brief")
    graph.add_edge("orchestrate", "brief")
    graph.add_edge("brief", "respond")
    graph.add_edge("respond", "record")
    graph.add_edge("record", END)

    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else get_checkpointer()
    )
