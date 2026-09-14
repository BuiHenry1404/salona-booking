"""Hình dạng graph khoá cứng: mọi câu LLM qua guard đúng một lần; rewrite tối đa một lần."""
from unittest.mock import MagicMock

from app.agents.booking_graph.graph import build_graph
from app.models.user import User


def edges():
    g = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan")).get_graph()
    return {(e.source, e.target) for e in g.edges}


def test_every_llm_node_feeds_the_guard():
    e = edges()
    for node in ("booking", "shop", "social"):
        assert (node, "guard") in e, node
        assert (node, "__end__") not in e, node


def test_guard_goes_to_rewrite_or_end_and_rewrite_returns_to_guard():
    e = edges()
    assert ("guard", "rewrite") in e and ("guard", "__end__") in e
    assert ("rewrite", "guard") in e
    assert ("rewrite", "__end__") not in e
