"""Cuộc trò chuyện = thread của Orchestrator (spec §3.4). Một document trong
collection `conversation_threads` — KHÔNG dùng lại `conversations` (lịch sử
chat nguyên văn của agent cũ) hay `conversation_sessions` (bản nén theo ngày
của agent cũ). `thread_id` của checkpointer LangGraph là `str(thread.id)`.
"""
from datetime import date, datetime
from typing import Any, Optional

from pydantic import field_validator

from app.models.base import BaseDocument


class ConversationThread(BaseDocument):
    user_id: str
    day: date                      # ngày VN cuộc trò chuyện mở; lưu dạng datetime nửa đêm trong Mongo
    started_at: datetime
    last_turn_at: datetime
    previous_id: Optional[str] = None

    @field_validator("day", mode="before")
    @classmethod
    def _datetime_to_date(cls, value: Any) -> Any:
        # Mongo không có kiểu date riêng — đọc lên phải tự ép lại, cùng quy
        # ước với `ConversationState.day`.
        return value.date() if isinstance(value, datetime) else value
