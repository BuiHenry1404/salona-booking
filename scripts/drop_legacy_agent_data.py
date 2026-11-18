"""Dọn dữ liệu của gói agent cũ (`app/agents/booking/`, xoá ở Task 16) — chạy
MỘT lần, tay, chạy lại bao nhiêu lần cũng được (idempotent).

    PYTHONPATH=. .venv/bin/python scripts/drop_legacy_agent_data.py          # chạy thử: chỉ đếm
    PYTHONPATH=. .venv/bin/python scripts/drop_legacy_agent_data.py --yes    # xoá thật

Xoá:
- collection `conversation_sessions` (bản nén theo ngày) và `turn_ledger`
  (sổ lượt của gói cũ) — gói `salon` dùng `salon_turns`, không đọc hai cái này;
- trường `state` (bản nén trong ngày) và `pending_confirmation` (cờ chờ xác
  nhận, đã thay bằng `proposals`) trong document `conversations` — giữ nguyên
  `user_id`, `messages`, `created_at`, `updated_at` (màn lịch sử của khách);
- checkpoint của thread kiểu cũ `<user_id>:<YYYY-MM-DD>` trong `checkpoints` /
  `checkpoint_writes`. Thread của gói `salon` là id của `conversation_threads`
  (ObjectId, không có dấu `:`) nên không bị đụng.

DB lấy từ `.env` (`MONGO_URI`, `MONGO_DB_NAME`) như app. Kiểm kỹ DB đích trước
khi thêm `--yes`.
"""
import argparse
import asyncio
from dataclasses import dataclass, field

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.agents.checkpoint import CHECKPOINT_COLLECTIONS
from app.core.config import settings

LEGACY_COLLECTIONS = ("conversation_sessions", "turn_ledger")
LEGACY_CONVERSATION_FIELDS = ("state", "pending_confirmation")
# `<user_id>:<YYYY-MM-DD>` — cách gói cũ đặt tên thread (một thread/khách/ngày VN).
OLD_THREAD_PATTERN = r"^[^:]+:\d{4}-\d{2}-\d{2}$"


@dataclass
class Counts:
    """Số document mỗi mục sẽ bị (hoặc đã bị) xoá."""

    collections: dict[str, int] = field(default_factory=dict)
    conversation_fields: int = 0
    checkpoints: dict[str, int] = field(default_factory=dict)


def _legacy_fields_filter() -> dict:
    return {"$or": [{name: {"$exists": True}} for name in LEGACY_CONVERSATION_FIELDS]}


def _old_thread_filter() -> dict:
    return {"thread_id": {"$regex": OLD_THREAD_PATTERN}}


async def count_legacy(db: AsyncIOMotorDatabase) -> Counts:
    existing = set(await db.list_collection_names())
    counts = Counts()
    for name in LEGACY_COLLECTIONS:
        counts.collections[name] = (
            await db[name].count_documents({}) if name in existing else 0)
    counts.conversation_fields = await db["conversations"].count_documents(
        _legacy_fields_filter())
    for name in CHECKPOINT_COLLECTIONS:
        counts.checkpoints[name] = await db[name].count_documents(_old_thread_filter())
    return counts


async def drop_legacy(db: AsyncIOMotorDatabase) -> Counts:
    """Xoá thật. Trả về số đã xoá (đếm ngay trước khi xoá)."""
    counts = await count_legacy(db)
    existing = set(await db.list_collection_names())
    for name in LEGACY_COLLECTIONS:
        if name in existing:
            await db.drop_collection(name)
    await db["conversations"].update_many(
        _legacy_fields_filter(),
        {"$unset": {name: "" for name in LEGACY_CONVERSATION_FIELDS}},
    )
    for name in CHECKPOINT_COLLECTIONS:
        await db[name].delete_many(_old_thread_filter())
    return counts


def _print(counts: Counts, *, done: bool) -> None:
    verb = "Đã xoá" if done else "Sẽ xoá"
    for name, n in counts.collections.items():
        print(f"  {verb} collection {name!r}: {n} document")
    fields = ", ".join(LEGACY_CONVERSATION_FIELDS)
    print(f"  {verb} trường ({fields}) ở {counts.conversation_fields} document 'conversations'")
    for name, n in counts.checkpoints.items():
        print(f"  {verb} {n} document thread kiểu cũ trong {name!r}")


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true",
                        help="xoá thật; không có cờ này thì chỉ chạy thử (đếm)")
    args = parser.parse_args(argv)

    client = AsyncIOMotorClient(str(settings.mongo_uri), tz_aware=True)
    db = client[settings.mongo_db_name]
    try:
        print(f"DB: {settings.mongo_db_name}")
        if args.yes:
            _print(await drop_legacy(db), done=True)
        else:
            _print(await count_legacy(db), done=False)
            print("Chạy thử — chưa xoá gì. Thêm --yes để xoá thật.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
