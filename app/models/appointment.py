from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field, field_validator

from app.core.text import single_line
from app.models.base import BaseDocument

Status = Literal["booked", "cancelled"]
CreatedVia = Literal["chat", "admin"]

# Ghi chú là tên dịch vụ ("làm tóc", "cắt tóc nhuộm nâu khói") — 80 ký tự là
# rộng rãi. Dài hơn thì nó lấn át khối bối cảnh thay vì mô tả một việc.
NOTE_MAX = 80


class Appointment(BaseDocument):
    """Một lần hẹn. user_name và phone được chụp lại lúc đặt để admin đọc
    danh sách mà không phải join sang users."""

    user_id: str
    user_name: Optional[str] = None
    phone: Optional[str] = None
    start_at: datetime
    duration_minutes: int
    slot_keys: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    status: Status = "booked"
    created_via: CreatedVia = "chat"

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value: Optional[str]) -> Optional[str]:
        """`note` do model ghi theo lời khách, rồi lượt sau được in lại vào
        khối bối cảnh và vào kết quả `list_my_appointments`. Cùng lối tiêm với
        `full_name`, nhưng trước đây không giới hạn gì cả.

        Lọc ở model để phủ cả hai đường đọc, và để ghi chú bẩn đã nằm sẵn
        trong DB cũng sạch lúc đọc lên.
        """
        return single_line(value, NOTE_MAX)
