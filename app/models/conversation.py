from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.core.clock import now_utc
from app.models.base import BaseDocument

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=now_utc)


class DaySummary(BaseModel):
    """Một dòng trong màn lịch sử trò chuyện của khách."""

    day: date
    message_count: int
    # Câu ĐẦU TIÊN khách nói hôm đó, cắt ngắn — để khách nhận ra hôm ấy nói
    # chuyện gì. Lấy câu của khách chứ không lấy câu mở đầu của AI, vì câu của
    # AI ngày nào cũng na ná nhau.
    preview: str


class Digest(BaseModel):
    """Bản nén phần cũ của hội thoại HÔM NAY. Không phải tầng 3 đã bỏ (ký ức
    xuyên phiên) — nó là bản nén của tầng 2, cắt theo ngày, sống trong chính
    document conversations. Xem spec 2026-09-14-conversation-digest-design.md.
    """

    day: date                        # ngày VN digest thuộc về; khác hôm nay là bỏ
    covers_until: datetime           # created_at của tin CUỐI đã được nén
    bullets: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_utc)
    failures: int = 0                # cầu chì: nén hỏng liên tiếp


class Conversation(BaseDocument):
    """Mỗi khách đúng MỘT document, chứa toàn bộ tin nhắn từ trước tới nay.

    Không có `session_id`. Ranh giới phiên được áp lúc ĐỌC (`history()`), không
    phải lúc ghi — nhờ vậy đổi quy tắc cắt phiên về sau không cần migrate gì.

    Document phình dần vì tin cũ không bị xoá, chỉ không được nạp. Mongo giới
    hạn 16MB mỗi document; với vài trăm khách và vài tin mỗi tuần thì còn hàng
    chục năm mới chạm, nên chưa xử — nhưng đừng quên là nó có trần.
    """

    user_id: str
    messages: List[ChatMessage] = Field(default_factory=list)
    pending_confirmation: Optional[Dict[str, Any]] = None
    digest: Optional[Digest] = None
