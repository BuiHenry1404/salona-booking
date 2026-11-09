from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.checkpoint import make_serde
from app.agents.salon.orchestrator.state import (
    RESET,
    SalonState,
    count_reducer,
    facts_reducer,
    read_facts,
    read_pending_outcome,
)
from app.agents.salon.shared.schemas import Moment, PendingOutcome, TimeFact


def test_facts_reducer_appends_and_resets():
    assert facts_reducer(["a"], ["b"]) == ["a", "b"]
    assert facts_reducer(["a"], RESET) == []
    assert facts_reducer(["a"], [RESET, "b"]) == ["b"]


def test_count_reducer_sums_and_resets():
    assert count_reducer(1, 1) == 2
    assert count_reducer(3, RESET) == 0


def test_two_tool_calls_in_one_superstep_merge_without_error():
    """Two parallel nodes writing `turn_facts` in the same superstep must
    merge through the reducer, not raise `InvalidUpdateError` (the default
    for a key written twice without one)."""

    class S(TypedDict, total=False):
        turn_facts: Annotated[list, facts_reducer]

    graph = StateGraph(S)
    graph.add_node("a", lambda s: {"turn_facts": ["a"]})
    graph.add_node("b", lambda s: {"turn_facts": ["b"]})
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)
    compiled = graph.compile()

    result = compiled.invoke({"turn_facts": []})

    assert sorted(result["turn_facts"]) == ["a", "b"]


def test_state_has_no_sessions_key():
    assert "sessions" not in SalonState.__annotations__
    assert "opened_sessions" not in SalonState.__annotations__


def test_state_roundtrips_through_the_project_checkpointer_serde():
    """`make_serde()` builds the same serializer the real checkpointer uses,
    without opening a `MongoClient` — this must stay DB-free."""
    moment = Moment(iso="2026-11-09T09:00:00+07:00", spoken="9 giờ sáng mai")
    fact = TimeFact(role="asked", moment=moment)
    outcome = PendingOutcome(outcome="approved", proposal_id="p1", kind="book", at=moment)

    state = {
        "turn_facts": [fact.model_dump(mode="json")],
        "pending_outcome": outcome.model_dump(mode="json"),
    }
    serde = make_serde()
    loaded = serde.loads_typed(serde.dumps_typed(state))

    assert read_facts(loaded) == [fact]
    assert read_pending_outcome(loaded) == outcome


def test_reset_sentinel_is_checkpoint_serializable():
    serde = make_serde()
    loaded = serde.loads_typed(serde.dumps_typed({"turn_facts": RESET}))
    assert loaded["turn_facts"] == RESET
