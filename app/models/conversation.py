from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.clock import now_utc
from app.models.base import BaseDocument

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
    # Không còn ghi (M3): cờ chờ xác nhận nay là đề xuất trong `proposals`
    # (ProposalService). Giữ field để document cũ vẫn đọc được.
    pending_confirmation: Optional[Dict[str, Any]] = None
    # Trường `state` (bản nén hội thoại của gói agent cũ, đã xoá ở Task 16)
    # không còn trong model; document cũ còn trường này vẫn đọc được vì
    # pydantic bỏ qua trường lạ.
