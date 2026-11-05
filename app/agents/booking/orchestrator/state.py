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

from langchain.agents.middleware import AgentState
from typing_extensions import NotRequired

from app.agents.booking.schemas import EndedBy


class OrchestratorState(AgentState):
    """§5.2 nguyên văn + phần mở rộng (vòng đã chạy, đếm reject liên tiếp)."""

    turn_context: NotRequired[dict]
    calls: NotRequired[list[dict]]
    proposals_created: NotRequired[int]
    pending_decided: NotRequired[bool]
    pending_outcome: NotRequired[dict | None]
    progress_markers: NotRequired[list[str]]
    no_progress_streak: NotRequired[int]
    # Vòng có ÍT NHẤT MỘT call được chạy (§7.1) — khác số lần model được gọi:
    # một vòng toàn bị từ chối không tăng trường này (§5.6).
    rounds_executed: NotRequired[int]
    # (h): 2 lần `decide_pending` liên tiếp bị veto cấu trúc → giữ nguyên đề
    # xuất thay vì ép dừng ngay lần đầu (tránh một veto đơn lẻ làm hỏng lượt).
    must_decide_rejections: NotRequired[int]
    force_end: NotRequired[bool]
    ended_by: NotRequired[EndedBy | None]
    # Tham số THẬT của lần gọi `finish`/`ask_customer` cuối — `OrchestratorGuard`
    # ghi lại để node `brief` (chạy sau khi vòng tool đã kết thúc) đọc, không
    # cần parse lại `messages`.
    finish_args: NotRequired[dict | None]
    ask_args: NotRequired[dict | None]
