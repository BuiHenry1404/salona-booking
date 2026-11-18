"""Phiên chờ của agent con (spec §5) — việc dở của Đặt lịch / Thông tin tiệm /
Dịch vụ qua các lượt chat. Sống trong collection `waiting_sessions`
(`WaitingSessionRepository`/`WaitingSessionService`), KHÔNG trong state của
graph: server khởi động lại vẫn còn, lượt bị huỷ giữa chừng vẫn giữ phiên đã
lưu. Mỗi khách tối đa một phiên cho mỗi agent con (unique index `(user_id,
agent)`); mở phiên mới cùng agent là ghi đè phiên cũ.

Hết hạn do code kiểm lúc đọc (`WaitingSessionService.active`), không phải TTL
Mongo — phiên `closed` vẫn ở lại document để lần `open` sau ghi đè.
"""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.base import BaseDocument


class SessionOption(BaseModel):
    """Một lựa chọn giờ/ngày code đưa ra kèm câu hỏi của agent con."""

    iso: str
    spoken: str


class SessionQuestion(BaseModel):
    """Câu hỏi ngắn agent con gửi lên Orchestrator để hỏi khách tiếp."""

    fields: list[str]
    options: list[SessionOption] = []
    text: str

    @model_validator(mode="before")
    @classmethod
    def _old_document_had_one_field(cls, data: Any) -> Any:
        """Phiên Mongo cũ còn `field: str` (trước khi hỏi gộp) — sống tối đa 10
        phút nên không cần chuyển dữ liệu; đọc như một phần tử (quyết định 21)."""
        if isinstance(data, dict) and "field" in data and "fields" not in data:
            data = dict(data)
            data["fields"] = [data.pop("field")]
        return data


class WaitingSession(BaseDocument):
    user_id: str
    agent: str
    task: str
    memory: dict[str, Any]
    question: SessionQuestion
    asked_at: datetime
    expires_at: datetime
    opened_turn_id: str
    status: Literal["waiting", "closed"] = "waiting"
    closed_reason: Optional[str] = None

    @field_validator("asked_at", "expires_at", mode="before")
    @classmethod
    def _ensure_session_tz(cls, v: Any) -> Any:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
