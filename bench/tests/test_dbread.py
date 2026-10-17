"""Test cho `bench/dbread.py`: diff thuần (không Mongo) + snapshot (Mongo thật)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient

from bench.dbread import ApptRow, diff, snapshot

MONGO_URI = "mongodb://localhost:27017"


def _row(id_: str, user_id: str, hour: int, status: str = "booked", note=None) -> ApptRow:
    return ApptRow(
        id=id_,
        user_id=user_id,
        start_at=datetime(2026, 10, 18, hour, 0, tzinfo=timezone.utc),
        status=status,
        note=note,
    )


def test_diff_detects_created_cancelled_and_moved():
    unchanged = _row("1", "u1", 9)
    to_cancel = _row("2", "u2", 10)
    to_move = _row("3", "u3", 11)

    before = [unchanged, to_cancel, to_move]
    after = [
        unchanged,
        _row("2", "u2", 10, status="cancelled"),
        _row("3", "u3", 15),  # dời giờ 11h -> 15h
        _row("4", "u4", 12),  # mới tạo
    ]

    changes = diff(before, after)

    assert [row.id for row in changes.created] == ["4"]
    assert [row.id for row in changes.cancelled] == ["2"]
    assert len(changes.other) == 1
    before_moved, after_moved = changes.other[0]
    assert before_moved.id == "3"
    assert after_moved.id == "3"
    assert after_moved.start_at.hour == 15


def test_diff_no_changes_when_snapshots_equal():
    rows = [_row("1", "u1", 9), _row("2", "u2", 10)]

    changes = diff(rows, list(rows))

    assert changes.created == []
    assert changes.cancelled == []
    assert changes.other == []


def test_snapshot_reads_appointments():
    db_name = f"bench_test_{uuid.uuid4().hex[:8]}"
    client: MongoClient = MongoClient(MONGO_URI)
    try:
        db = client[db_name]
        db["appointments"].insert_many(
            [
                {
                    "user_id": "u1",
                    "start_at": datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc),
                    "status": "booked",
                    "note": "cắt tóc",
                },
                {
                    "user_id": "u2",
                    "start_at": datetime(2026, 10, 18, 4, 30, tzinfo=timezone.utc),
                    "status": "cancelled",
                    "note": None,
                },
            ]
        )

        rows = snapshot(MONGO_URI, db_name)

        assert len(rows) == 2
        by_user = {row.user_id: row for row in rows}
        assert by_user["u1"].status == "booked"
        assert by_user["u1"].note == "cắt tóc"
        # start_at 03:00 UTC -> 10:00 giờ VN (+07:00).
        assert by_user["u1"].start_at.hour == 10
        assert by_user["u1"].start_at.utcoffset().total_seconds() == 7 * 3600
        assert by_user["u2"].status == "cancelled"
    finally:
        client.drop_database(db_name)
        client.close()
