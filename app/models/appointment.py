from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field

from app.models.base import BaseDocument

Status = Literal["booked", "cancelled"]
CreatedVia = Literal["chat", "admin"]


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
