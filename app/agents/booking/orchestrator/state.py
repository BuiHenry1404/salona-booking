"""`OrchestratorState` — §5.2 của thiết kế, cộng các trường đếm/kết thúc mà
`OrchestratorGuard` (task sau) cần để áp luật (g)/(h) và §5.6/§7.1.

Mọi trường đều `NotRequired`: `create_agent` dựng state ban đầu chỉ từ
`{"messages": [...]}`, các trường còn lại do middleware ghi dần bằng
`Command(update=...)` — không trường nào có giá trị mặc định ở đây, middleware
tự khởi tạo khi gặp lần đầu (tránh một nguồn sự thật thứ hai cho "giá trị rỗng
nghĩa là gì").

`calls`/`pending_outcome`/`turn_context`/`finish_args`/`ask_args` lưu dạng
`dict` (không phải lớp Pydantic): state của LangGraph đi qua checkpointer,
nên phải JSON-serializable trực tiếp — `model_dump(mode="json")` ở chỗ ghi,
`model_validate` ở chỗ đọc lại (nếu cần).
"""
from __future__ import annotations

import operator

from langchain.agents.middleware import AgentState
from typing_extensions import Annotated, NotRequired

from app.agents.booking.schemas import EndedBy

# Giá trị ghi vào `no_progress_streak` nghĩa là "đặt lại về 0" (call vừa rồi
# có marker mới) — mọi giá trị khác là SỐ CỘNG THÊM, không phải giá trị tuyệt
# đối. Xem `add_streak`.
STREAK_RESET = -1


def add_streak(current: int, update: int) -> int:
    """Reducer của `no_progress_streak`: `STREAK_RESET` → 0, còn lại cộng dồn.

    Các call CÙNG VÒNG chạy thành các task `Send` riêng trong cùng một
    superstep (`factory.py` `model_to_tools`), mỗi task trả một
    `Command(update=...)` — kênh không có reducer (LastValue) nhận >1 giá
    trị một bước sẽ ném `InvalidUpdateError`. Ghi delta (không ghi giá trị
    tuyệt đối đọc từ snapshot chung) để hai call trùng cùng vòng cộng được 2,
    không bị đếm thiếu thành 1. Thứ tự áp trong một bước là thứ tự task
    (= thứ tự call), nên [trùng, có marker] → 0 còn [có marker, trùng] → 1.
    """
    return 0 if update == STREAK_RESET else current + update


class OrchestratorState(AgentState):
    """§5.2 nguyên văn + phần mở rộng (vòng đã chạy, đếm reject liên tiếp)."""

    turn_context: NotRequired[dict]
    # Năm trường dưới đây CÓ reducer vì nhiều call cùng vòng có thể cùng ghi
    # trong một superstep (xem `add_streak`): `calls`/`progress_markers` nối
    # thêm, `proposals_created` cộng delta, `force_end` OR, `no_progress_streak`
    # delta + `STREAK_RESET`. Các trường còn lại chỉ một call/vòng được ghi
    # (luật (g) của `OrchestratorGuard` bảo đảm) hoặc chỉ `abefore_model` ghi.
    calls: NotRequired[Annotated[list[dict], operator.add]]
    proposals_created: NotRequired[Annotated[int, operator.add]]
    pending_decided: NotRequired[bool]
    pending_outcome: NotRequired[dict | None]
    progress_markers: NotRequired[Annotated[list[str], operator.add]]
    no_progress_streak: NotRequired[Annotated[int, add_streak]]
    # Vòng có ÍT NHẤT MỘT call được chạy (§7.1) — khác số lần model được gọi:
    # một vòng toàn bị từ chối không tăng trường này (§5.6).
    rounds_executed: NotRequired[int]
    # (h): số lần LIÊN TIẾP gần nhất một call bị từ chối `must_decide_pending`
    # (đếm lại từ `calls`, một call bị chặn ở (h) luôn là call duy nhất của
    # nhóm tuần tự nên chỉ một task ghi mỗi bước) — tới 2 thì code tự ghi
    # `kept` và `pending_decided=True`.
    must_decide_rejections: NotRequired[int]
    force_end: NotRequired[Annotated[bool, operator.or_]]
    ended_by: NotRequired[EndedBy | None]
    # Tham số THẬT của lần gọi `finish`/`ask_customer` cuối — `OrchestratorGuard`
    # ghi lại để node `brief` (chạy sau khi vòng tool đã kết thúc) đọc, không
    # cần parse lại `messages`.
    finish_args: NotRequired[dict | None]
    ask_args: NotRequired[dict | None]
