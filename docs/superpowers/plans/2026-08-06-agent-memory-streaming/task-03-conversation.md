# Task 3 · Hội thoại, lịch sử và cờ xác nhận

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Rewrite: `app/models/conversation.py`, `app/repositories/conversation.py`, `app/services/conversation.py`
- Delete: `app/models/task.py`, `app/repositories/task.py`, `app/services/task.py`, `app/api/v1/routers/tasks.py`, `app/api/v1/routers/conversations.py`
- Create: `tests/test_conversation.py`

**Interfaces:**
- Consumes: `now_utc` (Plan 1 task 3)
- Produces:
  - `ChatMessage(role: Literal["user","assistant"], content: str, created_at: datetime)`
  - `Conversation(user_id: str, messages: list[ChatMessage], pending_confirmation: dict | None)`
  - `ConversationService(db).get_or_create(user_id: str) -> Conversation`
  - `ConversationService(db).append(user_id: str, role: str, content: str) -> None`
  - `ConversationService(db).history(user_id: str, token_budget: int = 1500) -> list[ChatMessage]`
  - `ConversationService(db).set_pending(user_id: str, payload: dict | None) -> None`
  - `ConversationService(db).get_pending(user_id: str, max_age_minutes: int = 10) -> dict | None`

- [ ] **Step 1: Xóa phần task cũ của template**

```bash
git rm app/models/task.py app/repositories/task.py app/services/task.py \
       app/api/v1/routers/tasks.py app/api/v1/routers/conversations.py
git rm -f tests/test_chat.py 2>/dev/null || true
```

Trong `app/api/v1/routers/__init__.py`, xóa mọi dòng nhắc `tasks` hoặc `conversations`.

- [ ] **Step 2: Viết test (sẽ fail)**

Tạo `tests/test_conversation.py`:

```python
from datetime import timedelta

import pytest

from app.core.clock import now_utc
from app.services.conversation import ConversationService

pytestmark = pytest.mark.asyncio


async def test_append_then_read_history(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "mai 3h chiều được không")
    await svc.append("u1", "assistant", "dạ được ạ")

    history = await svc.history("u1")
    assert [(m.role, m.content) for m in history] == [
        ("user", "mai 3h chiều được không"),
        ("assistant", "dạ được ạ"),
    ]


async def test_history_is_isolated_per_user(test_db):
    svc = ConversationService(test_db)
    await svc.append("u1", "user", "của u1")
    assert await svc.history("u2") == []


async def test_history_is_trimmed_to_the_token_budget(test_db):
    svc = ConversationService(test_db)
    for i in range(200):
        await svc.append("u1", "user", "câu nói khá dài để tốn token " * 5)

    history = await svc.history("u1", token_budget=200)
    assert 0 < len(history) < 200


async def test_history_keeps_the_most_recent_messages(test_db):
    svc = ConversationService(test_db)
    for i in range(50):
        await svc.append("u1", "user", f"tin nhắn số {i} " * 10)

    history = await svc.history("u1", token_budget=200)
    assert "tin nhắn số 49" in history[-1].content


async def test_pending_confirmation_round_trip(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "2026-08-07T08:00:00+00:00", "note": "làm tóc"})
    pending = await svc.get_pending("u1")
    assert pending["note"] == "làm tóc"


async def test_pending_confirmation_expires(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "x", "note": "làm tóc"})
    stale = now_utc() - timedelta(minutes=30)
    await test_db["conversations"].update_one(
        {"user_id": "u1"}, {"$set": {"pending_confirmation.asked_at": stale}}
    )
    assert await svc.get_pending("u1", max_age_minutes=10) is None


async def test_clearing_pending(test_db):
    svc = ConversationService(test_db)
    await svc.set_pending("u1", {"start_at": "x", "note": None})
    await svc.set_pending("u1", None)
    assert await svc.get_pending("u1") is None
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_conversation.py -v`
Expected: FAIL — `ConversationService` cũ không có `append`/`history`

- [ ] **Step 4: Viết `app/models/conversation.py`**

```python
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.core.clock import now_utc
from app.models.base import BaseDocument

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=now_utc)


class Conversation(BaseDocument):
    """Mỗi khách có đúng một cuộc hội thoại chạy mãi — không chia phiên.

    Khách đặt lịch vài tuần một lần, nên cửa sổ trượt theo ngân sách token
    thường phủ được vài tháng mà vẫn liền mạch.
    """

    user_id: str
    messages: List[ChatMessage] = Field(default_factory=list)
    pending_confirmation: Optional[Dict[str, Any]] = None
```

- [ ] **Step 5: Viết `app/services/conversation.py`**

```python
from datetime import timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.models.conversation import ChatMessage, Conversation

# Ước lượng thô cho tiếng Việt: ~3 ký tự một token. Đủ chính xác để cắt lịch sử;
# đếm token thật cần tokenizer của model và không đáng cho việc này.
CHARS_PER_TOKEN = 3
DEFAULT_TOKEN_BUDGET = 1500


class ConversationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["conversations"]

    async def get_or_create(self, user_id: str) -> Conversation:
        doc = await self.collection.find_one({"user_id": user_id})
        if doc:
            return Conversation(**doc)
        now = now_utc()
        payload = {"user_id": user_id, "messages": [], "pending_confirmation": None,
                   "created_at": now, "updated_at": now}
        result = await self.collection.insert_one(payload)
        payload["_id"] = result.inserted_id
        return Conversation(**payload)

    async def append(self, user_id: str, role: str, content: str) -> None:
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"messages": ChatMessage(role=role, content=content).model_dump()},
                "$set": {"updated_at": now_utc()},
                "$setOnInsert": {"user_id": user_id, "created_at": now_utc()},
            },
            upsert=True,
        )

    async def history(
        self, user_id: str, token_budget: int = DEFAULT_TOKEN_BUDGET
    ) -> List[ChatMessage]:
        """Lấy ngược từ tin mới nhất cho tới khi chạm trần ngân sách.

        Cắt theo token chứ không theo số lượt: một khách nói dài dòng chiếm gấp
        nhiều lần một khách nói cộc lốc, nên đếm lượt là sai đơn vị.
        """
        doc = await self.collection.find_one({"user_id": user_id})
        if not doc:
            return []

        messages = [ChatMessage(**m) for m in doc.get("messages", [])]
        kept: List[ChatMessage] = []
        used = 0
        for message in reversed(messages):
            cost = max(1, len(message.content) // CHARS_PER_TOKEN)
            if used + cost > token_budget and kept:
                break
            kept.append(message)
            used += cost
        return list(reversed(kept))

    async def set_pending(self, user_id: str, payload: Optional[Dict[str, Any]]) -> None:
        value = {**payload, "asked_at": now_utc()} if payload else None
        await self.collection.update_one(
            {"user_id": user_id},
            {"$set": {"pending_confirmation": value, "updated_at": now_utc()},
             "$setOnInsert": {"user_id": user_id, "messages": [], "created_at": now_utc()}},
            upsert=True,
        )

    async def get_pending(
        self, user_id: str, max_age_minutes: int = 10
    ) -> Optional[Dict[str, Any]]:
        """Cờ hết hạn sau 10 phút: quá đó thì khách nói "ừ" cũng phải hỏi lại,
        vì nhiều khả năng họ đang nói về chuyện khác."""
        doc = await self.collection.find_one({"user_id": user_id})
        pending = (doc or {}).get("pending_confirmation")
        if not pending:
            return None

        asked_at = pending.get("asked_at")
        if asked_at is None:
            return None
        if asked_at.tzinfo is None:
            asked_at = asked_at.replace(tzinfo=now_utc().tzinfo)
        if now_utc() - asked_at > timedelta(minutes=max_age_minutes):
            return None
        return pending
```

- [ ] **Step 6: Viết lại `app/repositories/conversation.py`**

Xóa nội dung cũ (dựa trên `task`) và thay bằng:

```python
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.conversation import Conversation
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Conversation, "conversations")
```

- [ ] **Step 7: Thêm index cho conversations**

Trong `app/infrastructure/database.py`, thêm vào `ensure_indexes`:

```python
    await db["conversations"].create_index("user_id", unique=True)
```

- [ ] **Step 8: Chạy test để xác nhận pass**

Run: `pytest tests/test_conversation.py -v`
Expected: PASS (7 passed)

- [ ] **Step 9: Chạy toàn bộ test để chắc không vỡ gì**

Run: `pytest -v`
Expected: PASS toàn bộ

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: conversation history with token-budget trimming and pending confirmation"
```
