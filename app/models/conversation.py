from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.clock import now_utc
from app.models.base import BaseDocument

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=now_utc)
    # "code": câu do node confirm / câu lỗi sinh — cố ý giống nhau, không đem so
    # lặp. Document cũ không có trường này → mặc định "llm", không migrate.
    source: Literal["llm", "code"] = "llm"


class DaySummary(BaseModel):
    """Một dòng trong màn lịch sử trò chuyện của khách."""

    day: date
    message_count: int
    # Câu ĐẦU TIÊN khách nói hôm đó, cắt ngắn — để khách nhận ra hôm ấy nói
    # chuyện gì. Lấy câu của khách chứ không lấy câu mở đầu của AI, vì câu của
    # AI ngày nào cũng na ná nhau.
    preview: str


class ConversationSlots(BaseModel):
    """Dữ kiện dạng trường của cuộc trò chuyện hôm nay, do lượt nén (LLM) sinh.

    Đây là THÙNG CHỨA THÔ, cố ý không validate gì: nó là đích của
    `with_structured_output`, nên một field sai kiểu sẽ làm Pydantic ném lỗi
    và đánh trượt cả lượt nén — mất luôn phần summary vốn đúng, lại còn đốt
    một nấc cầu chì. Hàng rào nằm ở `sanitize_slots()`, chạy sau khi parse.

    Không có đường code nào đặt/dời/hủy lịch dựa trên các field này. Chúng chỉ
    được in thành chữ trong prompt, và luôn THUA khối bối cảnh.
    """

    intent: Optional[str] = None                 # "book" | "reschedule" | "cancel"
    service: Optional[str] = None                # text tự do, ≤ NOTE_MAX
    day: Optional[str] = None                    # "YYYY-MM-DD"
    time: Optional[str] = None                   # "HH:MM"
    target_appointment_id: Optional[str] = None
    declined: List[str] = Field(default_factory=list)   # ISO local, giờ khách đã lắc


class ConversationState(BaseModel):
    """Bản nén phần cũ của hội thoại HÔM NAY. Không phải tầng 3 đã bỏ (ký ức
    xuyên phiên) — nó là bản nén của tầng 2, cắt theo ngày, sống trong chính
    document conversations. Xem spec 2026-09-14-conversation-digest-design.md.
    """

    day: date                        # ngày VN state thuộc về; khác hôm nay là bỏ
    covers_until: datetime           # created_at của tin CUỐI đã được nén
    summary: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_utc)
    failures: int = 0                # cầu chì: nén hỏng liên tiếp
    slots: Optional[ConversationSlots] = None    # document cũ → None, không migrate

    @field_validator("day", mode="before")
    @classmethod
    def _datetime_to_date(cls, value):
        # Mongo lưu `day` dạng datetime (không có kiểu date riêng) — đọc lên
        # phải tự ép về date, Pydantic v2 không làm việc này mặc định.
        return value.date() if isinstance(value, datetime) else value


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
    state: Optional[ConversationState] = None
