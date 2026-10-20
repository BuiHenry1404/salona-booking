"""Checkpointer Mongo cho graph chat — lịch sử model theo thread `<user>:<ngày VN>`.

Ba điều không được đổi:

1. **DB = `settings.mongo_db_name`**, không phải DB mặc định `checkpointing_db`
   của thư viện. Benchmark tạo một DB riêng cho mỗi ref rồi xoá; checkpoint ở DB
   khác là rò state giữa các lượt đo, và dev/prod dọn DB không dọn được nó.
2. **`MongoClient` sync riêng.** `langgraph-checkpoint-mongodb==0.5.1` chỉ có
   `MongoDBSaver` (pymongo sync); bản async của nó chạy hàm sync trong thread
   executor. Motor của app không dùng lại được ở đây.
3. **Serializer có allowlist.** State mang `ConversationSlots` (pydantic); để
   mặc định thì thư viện cho giải mã MỌI kiểu (kèm cảnh báo). Chỉ mở đúng kiểu
   của mình.

TTL (`CHECKPOINT_TTL_DAYS`) chỉ là dọn rác. Hạn nghiệp vụ (10 phút của câu hỏi
xác nhận, cắt theo ngày của lịch sử) nằm ở code đọc, không dựa vào TTL.
"""
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pymongo import MongoClient

from app.core.config import settings

_checkpointer: Optional[BaseCheckpointSaver] = None


CHECKPOINT_COLLECTIONS = ("checkpoints", "checkpoint_writes")


def _sync_ttl(client: MongoClient, ttl: int) -> None:
    """Đổi `CHECKPOINT_TTL_DAYS` thì sửa index TTL đang có bằng `collMod`.

    Thư viện (0.5.1) so khoá index list với tuple nên luôn gọi `create_index`;
    cùng TTL thì vô hại, khác TTL thì Mongo ném "equivalent index already exists
    with different options" — ngay trong lifespan fail-hard, app không lên.
    """
    db = client[settings.mongo_db_name]
    existing = set(db.list_collection_names())
    for name in CHECKPOINT_COLLECTIONS:
        if name not in existing:
            continue
        for index in db[name].list_indexes():
            if (dict(index["key"]) == {"created_at": 1}
                    and "expireAfterSeconds" in index
                    and index["expireAfterSeconds"] != ttl):
                db.command("collMod", name,
                           index={"keyPattern": {"created_at": 1}, "expireAfterSeconds": ttl})


def make_checkpointer(client: Optional[MongoClient] = None) -> MongoDBSaver:
    client = client or MongoClient(str(settings.mongo_uri), tz_aware=True)
    ttl = settings.checkpoint_ttl_days * 86400
    _sync_ttl(client, ttl)
    return MongoDBSaver(
        client,
        db_name=settings.mongo_db_name,
        ttl=ttl,
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[("app.models.conversation", "ConversationSlots")]
        ),
    )


def get_checkpointer() -> BaseCheckpointSaver:
    """Checkpointer dùng chung của tiến trình. Lifespan đặt sẵn lúc khởi động;
    chưa có thì tạo ở lần gọi đầu."""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = make_checkpointer()
    return _checkpointer


def set_checkpointer(saver: Optional[BaseCheckpointSaver]) -> None:
    """Cho lifespan và test. `None` = quên checkpointer hiện tại."""
    global _checkpointer
    _checkpointer = saver
