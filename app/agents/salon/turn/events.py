"""Sự kiện gửi giao diện của một lượt (spec §3.2; Socket.IO ở Task 16).

Không còn `token`: câu tới khách chỉ đi qua `complete` sau guard. Từ sự kiện
thô của `astream_events` chỉ giữ lúc một tool BẮT ĐẦU / XONG — của Orchestrator
lẫn của agent con (config lan vào agent lồng qua contextvar) — kèm nhãn tiếng
Việt cho khách lớn tuổi đọc; tên tool kỹ thuật không bao giờ là nhãn.

Tool không có nhãn thì không phát: `decide_pending` (việc nội bộ), tool kết quả
`SubagentResult` của agent con, tool lạ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)


# Tên tool (Orchestrator + agent con) → nhãn cho khách. Thêm tool mới mà muốn
# khách thấy thì thêm dòng ở đây.
TOOL_LABELS: dict[str, str] = {
    # Orchestrator giao việc
    "appointment_task": "Đang xem lịch hẹn…",
    "shop_info_task": "Đang xem thông tin tiệm…",
    "service_task": "Đang xem dịch vụ và bảng giá…",
    # dùng chung
    "parse_time": "Đang xem lịch…",
    # agent Đặt lịch
    "find_free_slots": "Đang xem lịch trống…",
    "remember_next_request": "Đang ghi nhớ lịch tiếp theo…",
    "list_my_appointments": "Đang xem lịch của anh chị…",
    "propose_appointment": "Đang giữ chỗ cho anh chị…",
    "propose_cancel": "Đang chuẩn bị huỷ lịch…",
    # agent Thông tin tiệm
    "get_shop_hours": "Đang xem giờ mở cửa…",
    "get_shop_status": "Đang xem tiệm có rảnh không…",
    "is_open_at": "Đang xem giờ mở cửa…",
    # agent Dịch vụ & giá
    "search_services": "Đang tìm dịch vụ…",
    "get_service": "Đang xem bảng giá…",
    "list_services": "Đang xem bảng giá…",
}


def tool_event(raw: dict[str, Any]) -> Optional[AgentEvent]:
    """Một sự kiện `astream_events` v2 → `tool_started` / `tool_finished`, hoặc
    `None` (mọi sự kiện khác, kể cả `on_chat_model_stream`)."""
    name = raw.get("name")
    label = TOOL_LABELS.get(name) if isinstance(name, str) else None
    if label is None:
        return None
    kind = raw.get("event")
    if kind == "on_tool_start":
        return AgentEvent("tool_started", {"name": name, "label": label})
    if kind == "on_tool_end":
        return AgentEvent("tool_finished", {"name": name, "label": label, "ok": True})
    if kind == "on_tool_error":
        return AgentEvent("tool_finished", {"name": name, "label": label, "ok": False})
    return None
