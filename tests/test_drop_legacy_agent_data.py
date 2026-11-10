"""`scripts/drop_legacy_agent_data.py` — chỉ chạy trên DB test (`test_db`)."""
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from scripts import drop_legacy_agent_data as cleanup
from tests.conftest import TEST_DB_NAME, TEST_MONGO_URI

OLD_THREAD = "6af1c9a2c75a70d61c4d684b:2026-11-01"
NEW_THREAD = "6af1c9a2c75a70d61c4d684d"
AT = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)
MESSAGE = {"role": "user", "content": "chào", "created_at": AT, "source": "llm"}


async def _seed(db) -> None:
    await db["conversation_sessions"].insert_one({"user_id": "u1", "day": AT})
    await db["turn_ledger"].insert_many([{"turn_id": "t1"}, {"turn_id": "t2"}])
    await db["conversations"].insert_many([
        {"user_id": "u1", "messages": [MESSAGE], "created_at": AT, "updated_at": AT,
         "state": {"summary": ["x"]}, "pending_confirmation": {"start_at": "x"}},
        {"user_id": "u2", "messages": [MESSAGE], "created_at": AT, "updated_at": AT},
    ])
    for name in ("checkpoints", "checkpoint_writes"):
        await db[name].insert_many([
            {"thread_id": OLD_THREAD, "checkpoint_ns": "", "checkpoint_id": "a"},
            {"thread_id": NEW_THREAD, "checkpoint_ns": "", "checkpoint_id": "b"},
        ])


async def _snapshot(db) -> dict:
    names = set(await db.list_collection_names())
    return {
        "collections": sorted(n for n in cleanup.LEGACY_COLLECTIONS if n in names),
        "conversations": [
            {k: v for k, v in doc.items() if k != "_id"}
            async for doc in db["conversations"].find({}).sort("user_id", 1)],
        "checkpoints": {
            name: sorted([doc["thread_id"] async for doc in db[name].find({})])
            for name in ("checkpoints", "checkpoint_writes")},
    }


async def test_count_reports_what_would_be_removed_and_changes_nothing(test_db):
    await _seed(test_db)
    before = await _snapshot(test_db)

    counts = await cleanup.count_legacy(test_db)

    assert counts.collections == {"conversation_sessions": 1, "turn_ledger": 2}
    assert counts.conversation_fields == 1
    assert counts.checkpoints == {"checkpoints": 1, "checkpoint_writes": 1}
    assert await _snapshot(test_db) == before


async def test_drop_removes_only_legacy_data_and_is_idempotent(test_db):
    await _seed(test_db)

    removed = await cleanup.drop_legacy(test_db)

    assert removed.collections == {"conversation_sessions": 1, "turn_ledger": 2}
    after = await _snapshot(test_db)
    assert after["collections"] == []
    # Nhật ký hiển thị còn nguyên; chỉ mất hai trường cũ.
    assert after["conversations"] == [
        {"user_id": "u1", "messages": [MESSAGE], "created_at": AT, "updated_at": AT},
        {"user_id": "u2", "messages": [MESSAGE], "created_at": AT, "updated_at": AT},
    ]
    # Thread của gói salon (không có ":") không bị đụng.
    assert after["checkpoints"] == {"checkpoints": [NEW_THREAD], "checkpoint_writes": [NEW_THREAD]}

    again = await cleanup.drop_legacy(test_db)
    assert again.collections == {"conversation_sessions": 0, "turn_ledger": 0}
    assert again.conversation_fields == 0
    assert again.checkpoints == {"checkpoints": 0, "checkpoint_writes": 0}
    assert await _snapshot(test_db) == after


@pytest.fixture
def on_test_db(monkeypatch):
    """`main()` đọc DB từ settings — trỏ settings về DB test, không bao giờ DB dev."""
    monkeypatch.setattr(settings, "mongo_uri", TEST_MONGO_URI)
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)


async def test_without_yes_it_is_a_dry_run(test_db, on_test_db, capsys):
    await _seed(test_db)
    before = await _snapshot(test_db)

    await cleanup.main([])

    assert await _snapshot(test_db) == before
    out = capsys.readouterr().out
    assert f"DB: {TEST_DB_NAME}" in out and "--yes" in out and "Sẽ xoá" in out


async def test_with_yes_it_deletes(test_db, on_test_db, capsys):
    await _seed(test_db)

    await cleanup.main(["--yes"])

    assert (await _snapshot(test_db))["collections"] == []
    assert "Đã xoá" in capsys.readouterr().out
