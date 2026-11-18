from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.clock import now_utc

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=now_utc)
    # "code": câu do code viết (câu báo nút bấm, câu mẫu khi lỗi) — cố ý giống
    # nhau, không đem so lặp. Document cũ không có trường này → mặc định "llm",
    # không migrate.
    source: Literal["llm", "code"] = "llm"

    @field_validator("created_at", mode="before")
    @classmethod
    def _ensure_created_at_tz(cls, v: Any) -> Any:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class DaySummary(BaseModel):
    """Một dòng trong màn lịch sử trò chuyện của khách."""

    day: date
    message_count: int
    # Câu ĐẦU TIÊN khách nói hôm đó, cắt ngắn — để khách nhận ra hôm ấy nói
    # chuyện gì. Lấy câu của khách chứ không lấy câu mở đầu của AI, vì câu của
    # AI ngày nào cũng na ná nhau.
    preview: str
