"""Giao diện adapter chat cho benchmark (hộp đen — không `import app`).

Một `ChatAdapter` nói chuyện với một ref đang chạy giống hệt một khách hàng
thật: mở kết nối, gửi từng lượt (`Turn`), và ghi lại *mọi* sự kiện nhận được
kèm mốc thời gian tính từ lúc gửi tin. Giao thức hiện tại là Socket.IO
(`AdapterA`); nếu server đổi giao thức, thêm adapter mới thay vì sửa giao
thức chat/seed/đọc DB — ba mặt này tách biệt (xem spec mục 6 quyết định 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bench.dbread import ApptRow
    from bench.scenario import Turn
    from bench.seed import Customer


@dataclass(frozen=True)
class Event:
    """Một sự kiện nhận được từ server, với mốc ms tính từ lúc gửi tin."""

    t_ms: int
    type: str
    data: dict


@dataclass
class TurnRecord:
    """Kết quả ghi lại của một lượt chat."""

    index: int
    customer_text: str
    events: list[Event] = field(default_factory=list)
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    error: str | None = None
    first_token_ms: int | None = None
    complete_ms: int | None = None
    # Runner điền sau mỗi lượt (chụp DB sau khi lượt kết thúc) — adapter không đụng tới.
    db_after: "list[ApptRow] | None" = None


class ChatAdapter(Protocol):
    """Giao diện adapter chat: mở kết nối, gửi lượt, đóng kết nối.

    Một instance dùng cho đúng một cuộc hội thoại của một khách.
    """

    name: str

    async def open(self, base_url: str, customer: "Customer") -> None:
        """Mở kết nối tới `base_url`, xác thực bằng `customer.token`."""
        ...

    async def send(self, turn: "Turn", index: int, timeout_s: float) -> TurnRecord:
        """Gửi một lượt và chờ tới khi lượt kết thúc (complete/error/timeout)."""
        ...

    async def close(self) -> None:
        """Đóng kết nối, dọn tài nguyên."""
        ...
