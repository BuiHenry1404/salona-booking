# Task 8 · Nhánh xác nhận

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/confirm.py`, `tests/test_confirm.py`

**Interfaces:**
- Consumes: `AppointmentService` (Plan 1), `ConversationService` (task 3), `format_vi_datetime` (task 4)
- Produces:
  - `is_affirmative(text: str) -> bool`
  - `make_confirm_node(db, user) -> Callable[[GraphState], Awaitable[dict]]`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_confirm.py`:

```python
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import HumanMessage

from app.agents.booking_graph.confirm import is_affirmative, make_confirm_node
from app.core.clock import TZ
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.conversation import ConversationService

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("text", ["ừ", "Ừ", "đúng rồi", "ok", "OK ạ", "vâng", "dạ đúng", "được"])
def test_affirmative_phrases(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["không", "thôi khỏi", "đổi giờ khác", "để mai đi", "chưa"])
def test_negative_phrases(text):
    assert is_affirmative(text) is False


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def _setup(db):
    user = await AuthService(db).create_user("0912345678", "matkhau123", "Cô Lan")
    pending = {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}
    await ConversationService(db).set_pending(str(user.id), pending)
    return user


def a_state(user, text, pending):
    return {"messages": [HumanMessage(content=text)], "user_id": str(user.id),
            "context_block": "", "pending_confirmation": pending}


async def test_propose_then_yes_books_the_time_from_MONGO(test_db):
    """Test nối hai đầu: `propose_appointment` ghi cờ, `confirm` đọc lại.

    Điểm quan trọng: giá trị đem đi ghi lịch lấy từ Mongo, KHÔNG phải từ chuỗi
    model gõ lại ở lượt sau. Model chép sai 15:00 thành 5:00 cũng không ảnh
    hưởng, vì nó không còn được chạm vào con số đó nữa.
    """
    from app.agents.booking_graph.tools import make_booking_tools

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    start = tomorrow_at(15)

    tools = make_booking_tools(test_db, user)
    propose = next(t for t in tools if t.name == "propose_appointment")
    await propose.ainvoke({"start_at": start.isoformat(), "note": "làm tóc"})

    pending = await ConversationService(test_db).get_pending(str(user.id))
    result = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

    assert "xong" in result["answer"].lower()
    booked = await AppointmentService(test_db).upcoming_for(user)
    assert len(booked) == 1
    assert booked[0].start_at.astimezone(TZ) == start
    assert booked[0].note == "làm tóc"


async def test_yes_creates_the_appointment(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    node = make_confirm_node(test_db, user)
    result = await node(a_state(user, "ừ", pending))

    assert "xong" in result["answer"].lower()
    assert len(await AppointmentService(test_db).upcoming_for(user)) == 1


async def test_yes_clears_the_pending_flag(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    await make_confirm_node(test_db, user)(a_state(user, "đúng rồi", pending))
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_no_clears_the_flag_without_booking(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))

    result = await make_confirm_node(test_db, user)(a_state(user, "thôi khỏi", pending))

    assert await AppointmentService(test_db).upcoming_for(user) == []
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
    assert result["answer"]


async def test_taken_slot_produces_a_friendly_message_not_a_crash(test_db):
    user = await _setup(test_db)
    other = await AuthService(test_db).create_user("0938111222", "x", "Cô Hoa")
    await AppointmentService(test_db).create(other, tomorrow_at(15), note=None)

    pending = await ConversationService(test_db).get_pending(str(user.id))
    result = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))

    assert "có người" in result["answer"].lower()
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_confirm.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.confirm'`

- [ ] **Step 3: Viết `app/agents/booking_graph/confirm.py`**

```python
import re
from datetime import datetime
from typing import Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.core.errors import AppError
from app.core.logging import get_logger
from app.memory import remember
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService

logger = get_logger(__name__)

_YES = re.compile(
    r"\b(ừ|ừa|uh|um|ok|okay|okie|vâng|dạ|đúng|phải|được|duoc|dc|chuẩn|nhất trí|đồng ý)\b",
    re.IGNORECASE,
)
_NO = re.compile(
    r"\b(không|khong|ko|thôi|thoi|khỏi|đổi|doi|khác|khac|chưa|chua|hủy|huy)\b",
    re.IGNORECASE,
)


def is_affirmative(text: str) -> bool:
    """Phủ định thắng khẳng định: "dạ không" và "đúng rồi nhưng đổi giờ" đều phải
    ra False. Sai hướng này chỉ mất một câu hỏi lại; sai hướng kia là đặt nhầm lịch.
    """
    cleaned = (text or "").strip()
    if _NO.search(cleaned):
        return False
    return bool(_YES.search(cleaned))


def make_confirm_node(
    db: AsyncIOMotorDatabase, user: User
) -> Callable[[GraphState], Awaitable[dict]]:
    """Nhánh tắt cho câu trả lời xác nhận.

    Khách nói "ừ" sau khi AI đã hỏi "3h chiều Thứ Năm đúng không cô?" thì đi thẳng
    vào thực thi — không quay lại supervisor. Tiết kiệm một lượt LLM và loại bỏ
    hẳn vòng lặp hỏi xác nhận đi xác nhận lại.
    """
    service = AppointmentService(db)
    conversations = ConversationService(db)

    async def node(state: GraphState) -> dict:
        user_id = str(user.id)
        pending = state.get("pending_confirmation") or {}
        last_message = state["messages"][-1].content if state["messages"] else ""

        await conversations.set_pending(user_id, None)

        if not is_affirmative(last_message):
            return {"answer": "Dạ vâng, vậy cô chú muốn đặt ngày giờ nào ạ?"}

        try:
            appointment = await service.create(
                user, datetime.fromisoformat(pending["start_at"]), pending.get("note")
            )
        except AppError as exc:
            return {"answer": f"Dạ {exc.message} ạ. Cô chú chọn giờ khác giúp con nhé."}
        except (KeyError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return {"answer": "Dạ con nhầm mất rồi, cô chú nhắc lại ngày giờ giúp con ạ."}

        return {"answer": f"Xong rồi ạ. Hẹn gặp cô chú {format_vi_datetime(appointment.start_at)} nhé."}

    return node
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_confirm.py -v`
Expected: PASS (18 passed — 13 tham số hóa + 5 test tích hợp) — quan trọng nhất là `test_propose_then_yes_books_the_time_from_MONGO`

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/confirm.py tests/test_confirm.py
git commit -m "feat: confirmation shortcut that skips the supervisor"
```
