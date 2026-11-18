"""Store bộ nhớ dài hạn (spec §3.5) — singleton `BaseStore` dùng chung cho cả
tiến trình, cùng khớp lệnh `get_checkpointer()`/`set_checkpointer()` của
`app/agents/checkpoint.py`.

`make_store()` dựng một `MongoDBStore` trên collection pymongo ĐỒNG BỘ
`salon_memories`, dùng chung `MongoClient` với checkpointer qua
`get_sync_client()` (một client, một connection pool cho cả hai).

Step 0 (xác nhận bằng `inspect`, `langgraph-store-mongodb==0.4.0`):
- `MongoDBStore.__init__(collection, ttl_config=None, index_config=None, ...)`
  tự tạo index UNIQUE `(namespace_str, key)` ngay trong constructor (và tự
  backfill `namespace_str` cho document cũ) — không có bước `setup()` riêng
  để gọi thêm, khác hẳn vài store khác trong hệ `langgraph`.
- `MongoDBStore.abatch` đã là coroutine THẬT (không rơi vào
  `NotImplementedError` của `BaseStore.abatch` — lớp cha khai nó abstract):
  nó tự bọc `self.batch` (đồng bộ) bằng `langgraph.utils.config.run_in_executor`.
  Vì vậy KHÔNG cần bọc lại `batch` ở đây.
- Không truyền `index_config` (không tìm kiếm ngữ nghĩa — spec §3.5: Mongo
  của dự án là `mongod` thường, không có Atlas Vector Search) và không
  truyền `ttl_config` (bộ nhớ khách sống lâu dài, không có hạn).
"""
from typing import Optional

from langgraph.store.base import BaseStore
from langgraph.store.mongodb import MongoDBStore

from app.agents.checkpoint import get_sync_client
from app.core.config import settings

MEMORY_COLLECTION = "salon_memories"

_store: Optional[BaseStore] = None


def make_store() -> MongoDBStore:
    collection = get_sync_client()[settings.mongo_db_name][MEMORY_COLLECTION]
    return MongoDBStore(collection)


def get_store() -> BaseStore:
    """Store dùng chung của tiến trình. Lifespan đặt sẵn lúc khởi động; chưa
    có thì tạo ở lần gọi đầu."""
    global _store
    if _store is None:
        _store = make_store()
    return _store


def set_store(store: Optional[BaseStore]) -> None:
    """Cho lifespan và test (như `set_checkpointer`). `None` = quên store
    hiện tại."""
    global _store
    _store = store
