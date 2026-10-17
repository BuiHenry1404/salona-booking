"""Checkpointer Mongo: nằm trong DB của app (`MONGO_DB_NAME`), TTL theo setting.

Ràng buộc spec (Benchmark #5): KHÔNG dùng DB mặc định `checkpointing_db` của
langgraph-checkpoint-mongodb — benchmark tạo một DB riêng cho mỗi ref rồi xoá,
checkpoint nằm ngoài DB đó là rò dữ liệu giữa các lượt đo.
"""
from typing import TypedDict

import pytest
from pymongo import MongoClient

from app.core.config import settings
from tests.conftest import TEST_DB_NAME, TEST_MONGO_URI


@pytest.fixture
def sync_client():
    client = MongoClient(TEST_MONGO_URI, tz_aware=True)
    yield client
    client.close()


async def test_checkpoints_live_in_the_settings_db_not_checkpointing_db(
        monkeypatch, test_db, sync_client):
    from langgraph.graph import END, START, StateGraph

    from app.agents.checkpoint import make_checkpointer
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    saver = make_checkpointer(sync_client)
    assert saver.checkpoint_collection.database.name == TEST_DB_NAME
    assert saver.writes_collection.database.name == TEST_DB_NAME

    class S(TypedDict):
        x: int

    g = StateGraph(S)
    g.add_node("n", lambda s: {"x": 1})
    g.add_edge(START, "n")
    g.add_edge("n", END)
    await g.compile(checkpointer=saver).ainvoke(
        {"x": 0}, {"configurable": {"thread_id": "u:2026-10-17"}})
    assert "checkpoints" in await test_db.list_collection_names()


def test_ttl_index_uses_the_setting(monkeypatch, sync_client):
    from app.agents.checkpoint import make_checkpointer
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    monkeypatch.setattr(settings, "checkpoint_ttl_days", 2)
    saver = make_checkpointer(sync_client)
    for collection in (saver.checkpoint_collection, saver.writes_collection):
        ttl = [i for i in collection.index_information().values() if "expireAfterSeconds" in i]
        assert ttl and ttl[0]["expireAfterSeconds"] == 2 * 86400


def test_ttl_default_is_seven_days():
    assert type(settings).model_fields["checkpoint_ttl_days"].default == 7


def test_env_example_documents_the_ttl():
    from pathlib import Path
    text = Path(__file__).resolve().parent.parent.joinpath(".env.example").read_text()
    assert "CHECKPOINT_TTL_DAYS=7" in text


async def test_state_with_slots_and_datetimes_round_trips(monkeypatch, sync_client):
    """State lượt chat mang ConversationSlots (pydantic) và datetime. Serializer
    phải đọc lại được đúng kiểu — không thì lượt sau mất slots hoặc nổ."""
    from datetime import datetime, timezone
    from typing import Any, Optional

    from langgraph.graph import END, START, StateGraph

    from app.agents.checkpoint import make_checkpointer
    from app.models.conversation import ConversationSlots
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    saver = make_checkpointer(sync_client)

    class S(TypedDict, total=False):
        slots: Optional[Any]
        at: Optional[datetime]

    g = StateGraph(S)
    g.add_node("n", lambda s: {})
    g.add_edge(START, "n")
    g.add_edge("n", END)
    graph = g.compile(checkpointer=saver)
    at = datetime(2026, 10, 17, 3, 4, 5, 6000, tzinfo=timezone.utc)
    config = {"configurable": {"thread_id": "u:roundtrip"}}
    await graph.ainvoke({"slots": ConversationSlots(intent="book", service="làm tóc"), "at": at},
                        config)
    values = (await graph.aget_state(config)).values
    assert isinstance(values["slots"], ConversationSlots)
    assert values["slots"].service == "làm tóc"
    assert values["at"] == at


def test_get_checkpointer_returns_what_was_set():
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents import checkpoint
    saver = InMemorySaver()
    checkpoint.set_checkpointer(saver)
    assert checkpoint.get_checkpointer() is saver


def test_lifespan_installs_a_checkpointer_in_the_app_db_and_forgets_it_on_shutdown():
    from fastapi.testclient import TestClient

    from app.agents import checkpoint
    from main import app
    with TestClient(app):
        saver = checkpoint._checkpointer
        assert saver.checkpoint_collection.database.name == settings.mongo_db_name
    assert checkpoint._checkpointer is None


def test_tests_get_an_in_memory_checkpointer(memory_checkpointer):
    from app.agents.checkpoint import get_checkpointer
    assert get_checkpointer() is memory_checkpointer
