from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ShopHours(BaseModel):
    """Giờ mở cửa. closed_days theo quy ước 0 = Chủ nhật ... 6 = Thứ Bảy."""

    open_time: str = "08:00"
    close_time: str = "19:00"
    closed_days: List[int] = Field(default_factory=list)


class ShopStatusView(BaseModel):
    """Trạng thái tiệm đã tính sẵn cho hiển thị và cho AI đọc."""

    is_busy: bool
    busy_until: Optional[datetime] = None
    minutes_left: Optional[int] = None
