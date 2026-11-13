"""State schema of the Salon Orchestrator graph — spec §3.4, luật 8
docs/architech-agent.md.

Every value kept in the graph state must be plain JSON (dict / list / str /
int), never a pydantic object: the project checkpointer serde allows no
project model to roundtrip (`app/agents/checkpoint.py`). Every pydantic model
used here (`TurnFact`, `PendingOutcome`) is written with
`model.model_dump(mode="json")` and read back with the helpers below.

Waiting sessions are NOT part of this state (spec §5): subagents read/write
them through `WaitingSessionService` against Mongo, not through graph state —
that is the whole point of keeping them out: a server restart or a superseded
turn must not lose them, and two subagents writing in the same superstep must
not need a reducer.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from langchain.agents import AgentState
from pydantic import TypeAdapter
from typing_extensions import NotRequired

from app.agents.salon.shared.schemas import PendingOutcome, TurnFact

# Sentinel a reducer recognises as "clear this key for the new turn". A plain
# string, not `object()` — the checkpointer serializes state to msgpack/json,
# and an `object()` sentinel does not survive that roundtrip (same reasoning
# as langgraph's own `REMOVE_ALL_MESSAGES`).
RESET = "__salon_reset__"

_turn_fact_adapter: TypeAdapter = TypeAdapter(TurnFact)
_pending_outcome_adapter: TypeAdapter = TypeAdapter(PendingOutcome)


def facts_reducer(old: list, new: Any) -> list:
    """Accumulate `turn_facts` / `failed_calls` across one turn.

    - `new == RESET` (the string sentinel) clears the list — `before_agent`
      resetting turn-scoped state at the start of a new turn.
    - `new` is a list whose first element is `RESET` — a single write both
      resets and appends in the same superstep; keep everything after the
      sentinel.
    - otherwise — plain append, `old + new`. Two subagents writing in the same
      superstep each contribute their own `new` and LangGraph calls the
      reducer once per write, so two parallel tool calls merge without an
      `InvalidUpdateError`.
    """
    if new == RESET:
        return []
    if isinstance(new, list) and new and new[0] == RESET:
        return new[1:]
    return old + new


def count_reducer(old: int, new: Any) -> int:
    """Same RESET convention as `facts_reducer`, for integer counters."""
    if new == RESET:
        return 0
    return old + new


class SalonState(AgentState):
    """Extends `langchain.agents.AgentState` (brings `messages`) with the
    per-turn keys of spec §3.4. No `sessions` / `opened_sessions` key here —
    waiting sessions live in Mongo, see the module docstring."""

    turn_facts: NotRequired[Annotated[list[dict], facts_reducer]]
    proposals_created: NotRequired[Annotated[int, count_reducer]]
    # Names of subagents that failed in this turn — reset/appended like turn_facts.
    failed_calls: NotRequired[Annotated[list[str], facts_reducer]]
    turn_id: NotRequired[str]
    turn_kind: NotRequired[Literal["typed", "button"]]
    pending_decided: NotRequired[bool]
    pending_outcome: NotRequired[Optional[dict]]
    decide_calls: NotRequired[int]
    guard_rewrites: NotRequired[int]
    # `turn_id` trong sổ lượt mà câu trả lời được guard NHẬN đã báo (trích dẫn
    # quyết định / R0 với vai trò booked / moved / cancelled / failed) —
    # `run_turn` đọc để `mark_announced` (spec level C §5).
    announced_turn_ids: NotRequired[list[str]]


def read_facts(state) -> list[TurnFact]:
    """`state["turn_facts"]` (plain dicts) parsed back into `TurnFact` models."""
    return [_turn_fact_adapter.validate_python(fact) for fact in state.get("turn_facts", [])]


def read_pending_outcome(state) -> Optional[PendingOutcome]:
    """`state["pending_outcome"]` (plain dict or None) parsed back into `PendingOutcome`."""
    raw = state.get("pending_outcome")
    return _pending_outcome_adapter.validate_python(raw) if raw is not None else None


@dataclass
class SalonContext:
    """`context_schema` of `create_agent` — per-invocation, never persisted."""

    user_id: str
    turn_id: str
    thread_id: str
    asked_at: datetime
    question: str                    # what the customer typed; "" on a button press
    preset: Optional[PendingOutcome]  # the button's decision; None when the customer typed
