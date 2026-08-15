# Task 4 · State và khối bối cảnh tất định

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/__init__.py`, `app/agents/booking_graph/state.py`, `app/agents/booking_graph/context.py`, `tests/test_context_block.py`

**Interfaces:**
- Consumes: `ShopStatusView` (Plan 1 task 8), `Appointment` (Plan 1 task 7), `User` (Plan 1 task 4), `to_local` (Plan 1 task 3), `ConversationService` (task 3)
- Produces:
  - `state.py`: `GraphState` (TypedDict)
  - `context.py`: `build_context_block(user, status, upcoming) -> str`, `load_context(db, user, question) -> dict`, `format_vi_datetime(dt) -> str`

- [x] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_context_block.py`:

```python
from datetime import datetime

import pytest

from app.agents.booking_graph.context import (build_context_block,
                                              format_vi_datetime)
from app.core.clock import TZ
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.models.user import User


def a_user(name="Nguyễn Thị Lan"):
    return User(phone="0912345678", hashed_password="h", full_name=name, role="user")


def an_appointment():
    return Appointment(
        user_id="u1", user_name="Nguyễn Thị Lan", phone="0912345678",
        start_at=datetime(2026, 8, 7, 8, 0, tzinfo=TZ),
        duration_minutes=60, note="làm tóc",
    )


# `build_context_block` không còn tham số `memories`: tầng ngữ nghĩa đã bỏ cùng
# Mem0 (xem README, "số 2 bỏ trống"). Câu "tin phần này" vẫn giữ — nó chống cả
# chuyện model nhặt nhầm tên từ lịch sử chat.


def test_block_tells_the_model_what_day_it_is_today():
    """Không có dòng này thì "mai 3h chiều" là câu không giải được.

    Tool đòi `day` dạng YYYY-MM-DD và `start_at` dạng ISO, nhưng model không có
    cách nào biết hôm nay là ngày mấy. Thiếu mốc, nó suy ra từ dữ liệu huấn
    luyện và đặt lịch lệch cả năm — mà tool chỉ nhận được một chuỗi ngày hợp lệ
    nên không có gì để chặn.
    """
    now = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [], now=now)

    assert "2026-08-07" in block          # dạng máy, để model tự cộng ngày
    assert "Thứ Sáu" in block             # dạng người, để model nói lại cho khách
    assert "2:30 chiều" in block


def test_the_date_line_comes_first():
    """Đứng cuối khối thì model hay bỏ qua khi khối dài."""
    now = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [], now=now)
    assert block.splitlines()[0].startswith("Bây giờ là")


def test_block_contains_the_real_name_from_the_database():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "Nguyễn Thị Lan" in block
    assert "0912345678" in block


def test_block_says_it_outranks_anything_else_in_the_prompt():
    """Bảo vệ chống lỗi gọi nhầm tên: lịch sử chat có thể chứa tên người khác
    (khách nhắc tên con cháu). Khối này phải tự tuyên bố là nguồn đúng."""
    block = build_context_block(a_user("Nguyễn Thị Lan"), ShopStatusView(is_busy=False), [])
    assert "Nguyễn Thị Lan" in block
    assert "tin phần" in block.lower() or "ưu tiên" in block.lower()


def test_busy_status_gives_a_finish_TIME_not_a_countdown():
    """AI sẽ nhắc lại câu này cho khách, và nó nằm lại trong lịch sử chat.
    "Còn 30 phút" đọc lại sau một tiếng là sai hẳn."""
    block = build_context_block(
        a_user(),
        ShopStatusView(
            is_busy=True,
            busy_until=datetime(2026, 8, 7, 15, 30, tzinfo=TZ),
            minutes_left=30,
        ),
        [],
        now=datetime(2026, 8, 7, 15, 0, tzinfo=TZ),
    )
    assert "bận" in block.lower()
    assert "3:30 chiều" in block
    assert "30 phút" not in block


def test_free_status_is_written_in_words():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "rảnh" in block.lower()


def test_upcoming_appointments_appear_with_vietnamese_dates():
    block = build_context_block(
        a_user(), ShopStatusView(is_busy=False), [an_appointment()]
    )
    assert "làm tóc" in block
    assert "Thứ Sáu" in block  # 7/8/2026 là Thứ Sáu


def test_no_upcoming_appointments_is_stated_explicitly():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "chưa có lịch" in block.lower()


def test_format_vi_datetime():
    assert format_vi_datetime(datetime(2026, 8, 7, 15, 0, tzinfo=TZ)) == "Thứ Sáu 7/8, 3:00 chiều"
    assert format_vi_datetime(datetime(2026, 8, 7, 9, 30, tzinfo=TZ)) == "Thứ Sáu 7/8, 9:30 sáng"
```

- [x] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_context_block.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph'`

- [x] **Step 3: Viết `app/agents/booking_graph/state.py`**

```python
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    """State của một lượt chat. Mọi node đọc và ghi qua đây — không biến toàn cục,
    nên từng node kiểm thử độc lập được."""

    messages: Annotated[List[AnyMessage], add_messages]
    user_id: str
    context_block: str
    pending_confirmation: Optional[Dict[str, Any]]
    route: str
    answer: str
```

- [x] **Step 4: Viết `app/agents/booking_graph/context.py`**

```python
import asyncio
from datetime import datetime
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.shop import ShopService

_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def format_vi_datetime(dt) -> str:
    """'Thứ Sáu 7/8, 3:00 chiều' — cách người Việt lớn tuổi thực sự nói giờ."""
    local = to_local(dt)
    hour = local.hour
    if hour < 12:
        period, display = "sáng", hour
    elif hour < 18:
        period, display = "chiều", hour - 12 if hour > 12 else 12
    else:
        period, display = "tối", hour - 12
    return (f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, "
            f"{display}:{local.minute:02d} {period}")


def build_context_block(
    user: User,
    status: ShopStatusView,
    upcoming: List[Appointment],
    now: Optional[datetime] = None,
) -> str:
    """Khối bối cảnh dựng hoàn toàn bằng code — LLM không bao giờ sinh ra nó.

    Đây là nguồn duy nhất cho danh tính khách; không gì trong prompt được ghi đè
    nó. Đặt sát cuối prompt (không phải trong system prompt) để giữ tiền tố ổn
    định cho prompt caching.
    """
    local_now = to_local(now or now_utc())

    lines = [
        # Mốc thời gian phải đứng ĐẦU TIÊN.
        #
        # Khách nói "mai", "chiều nay", "thứ Năm tuần sau" — model không có cách
        # nào biết hôm nay là ngày nào. Thiếu dòng này thì nó suy ra từ mốc thời
        # gian trong dữ liệu huấn luyện và đặt lịch lệch cả năm, mà tool chỉ thấy
        # một chuỗi YYYY-MM-DD hợp lệ nên không có gì để chặn.
        #
        # Kèm luôn dạng ISO để model khỏi phải tự cộng trừ ngày rồi tính nhầm.
        f"Bây giờ là {format_vi_datetime(local_now)} "
        f"(hôm nay là {local_now.date().isoformat()}, giờ Việt Nam).",
        f"Bạn đang nói chuyện với: {user.full_name or 'khách'} ({user.phone}).",
    ]

    if status.is_busy:
        # Mốc giờ, không phải khoảng. Khối này đi vào prompt và AI sẽ nhắc lại
        # cho khách; "còn 30 phút" nằm lại trong lịch sử chat là sai vĩnh viễn.
        lines.append(f"Chủ tiệm: đang bận, xong lúc {format_vi_datetime(status.busy_until)}.")
    else:
        lines.append("Chủ tiệm: đang rảnh.")

    if upcoming:
        lines.append("Lịch sắp tới của khách:")
        for appt in upcoming[:5]:
            note = f" — {appt.note}" if appt.note else ""
            lines.append(f"  - {format_vi_datetime(appt.start_at)}{note}")
    else:
        lines.append("Khách chưa có lịch nào sắp tới.")

    # Dòng chốt: nếu lịch sử chat nói khác (khách nhắc tên con cháu, nhắc một
    # giờ hẹn đã đổi), thì phần này mới là đúng.
    lines.append(
        "Nếu có gì trong cuộc trò chuyện mâu thuẫn với phần trên, hãy tin phần trên."
    )

    return "\n".join(lines)


async def load_context(db: AsyncIOMotorDatabase, user: User, question: str) -> dict:
    """Nạp mọi thứ song song. Memory chạy cùng lúc với Mongo nên không cộng độ trễ."""
    conversations = ConversationService(db)
    user_id = str(user.id)

    status, upcoming, history, pending = await asyncio.gather(
        ShopService(db).get_status(),
        AppointmentService(db).upcoming_for(user),
        conversations.history(user_id),
        conversations.get_pending(user_id),
    )

    return {
        "context_block": build_context_block(user, status, upcoming),
        "history": history,
        "pending_confirmation": pending,
    }
```

- [x] **Step 5: Tạo `app/agents/booking_graph/__init__.py`**

```python
```

(file rỗng — nội dung export sẽ thêm ở task 9)

- [x] **Step 6: Chạy test để xác nhận pass**

Run: `pytest tests/test_context_block.py -v`
Expected: PASS (10 passed) — quan trọng nhất là `test_block_tells_the_model_what_day_it_is_today`

- [x] **Step 7: Commit**

```bash
git add app/agents/booking_graph tests/test_context_block.py
git commit -m "feat: deterministic context block that memory cannot override"
```
