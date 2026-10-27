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


class AskedItem(BaseModel):
    """Một câu bot đã hỏi, do CODE dựng (không phải lượt nén LLM) — xem
    `ConversationStateService.apply_turn_analysis`."""

    question: str
    answered: bool = False
    times: int = 1


class AgreedAt(BaseModel):
    """Khách đã đồng ý một đề xuất lúc nào — CHỈ để viết câu, không phải căn
    cứ để ghi lịch (việc ghi lịch vẫn qua node confirm đọc DB như cũ)."""

    proposal_id: str
    at: datetime


class ConversationSlots(BaseModel):
    """Dữ kiện dạng trường của cuộc trò chuyện hôm nay, do lượt nén (LLM) sinh.

    Đây là THÙNG CHỨA THÔ, cố ý không validate gì: nó là đích của
    `with_structured_output`, nên một field sai kiểu sẽ làm Pydantic ném lỗi
    và đánh trượt cả lượt nén — mất luôn phần summary vốn đúng, lại còn đốt
    một nấc cầu chì. Hàng rào nằm ở `sanitize_slots()`, chạy sau khi parse.

    Không có đường code nào đặt/dời/hủy lịch dựa trên các field này. Chúng chỉ
    được in thành chữ trong prompt, và luôn THUA khối bối cảnh.

    `stated`, `asked`, `told`, `agreed_at` là 4 nhóm MỚI (spec mục 3, bộ phân
    tích): `stated` do bộ phân tích mỗi lượt ghi thẳng (không qua LLM nén);
    `asked`/`told`/`agreed_at` do CODE dựng. Để chung model vì cả hai loại đều
    sống trong `ConversationState.slots` và được `render_slots` in ra cùng
    một khối — tách model riêng chỉ thêm một chỗ nữa phải đồng bộ.
    """

    intent: Optional[str] = None                 # "book" | "reschedule" | "cancel"
    service: Optional[str] = None                # text tự do, ≤ NOTE_MAX
    day: Optional[str] = None                    # "YYYY-MM-DD"
    time: Optional[str] = None                   # "HH:MM"
    declined: List[str] = Field(default_factory=list)   # ISO local, giờ khách đã lắc
    stated: List[str] = Field(default_factory=list)     # điều khách đã nói rõ/từ chối, ≤5
    asked: List[AskedItem] = Field(default_factory=list)  # câu bot đã hỏi, ≤5
    told: List[str] = Field(default_factory=list)       # điều bot đã khẳng định, ≤5
    agreed_at: Optional[AgreedAt] = None


class StateDigest(BaseModel):
    """Bản tóm tắt CUỐN CHIẾU theo 5 mục cố định (spec mục 3, đoạn "Nén giữ
    lại, đổi cách tóm", thêm 25/10/2026) — thay cho `summary` tự do kiểu gạch
    đầu dòng của Task 12.

    THÙNG CHỨA THÔ như `ConversationSlots`: không validate gì ở đây (đích của
    `with_structured_output`, một field sai kiểu sẽ đánh trượt cả lượt nén).
    Lọc độ dài/nội dung và cắt trần nằm ở `sanitize_digest`
    (`conversation_state.py`), cuốn chiếu (giữ `wants`/`constraints` khi bản
    mới rỗng) nằm ở `merge_digest`.
    """

    wants: List[str] = Field(default_factory=list)        # khách muốn gì — kể cả đổi ý; ≤4
    settled: List[str] = Field(default_factory=list)      # đã chốt/đã ghi; ≤4
    declined: List[str] = Field(default_factory=list)     # đã từ chối (ngoài phạm vi/của người khác); ≤4
    constraints: List[str] = Field(default_factory=list)  # ràng buộc khách nêu; ≤4
    open: List[str] = Field(default_factory=list)         # còn dang dở; ≤3


class ConversationState(BaseModel):
    """Bản nén phần cũ của hội thoại HÔM NAY. Không phải tầng 3 đã bỏ (ký ức
    xuyên phiên) — nó là bản nén của tầng 2, cắt theo ngày, sống trong chính
    document conversations. Xem spec
    docs/superpowers/specs/2026-09-19-conversation-state-design.md.
    """

    day: date                        # ngày VN state thuộc về; khác hôm nay là bỏ
    covers_until: datetime           # created_at của tin CUỐI đã được nén
    # `summary` giữ lại CHỈ để tài liệu cũ (trước Task 12c) vẫn đọc được —
    # lượt nén từ giờ ghi `digest`, không ghi thêm vào `summary` nữa.
    summary: List[str] = Field(default_factory=list)
    digest: Optional[StateDigest] = None          # document cũ → None, không migrate
    customer_quotes: List[str] = Field(default_factory=list)  # câu gốc khách, do code chọn
    updated_at: datetime = Field(default_factory=now_utc)
    failures: int = 0                # cầu chì: nén hỏng liên tiếp
    slots: Optional[ConversationSlots] = None    # document cũ → None, không migrate

    @field_validator("day", mode="before")
    @classmethod
    def _datetime_to_date(cls, value):
        # Mongo lưu `day` dạng datetime (không có kiểu date riêng) — đọc lên
        # phải tự ép về date, Pydantic v2 không làm việc này mặc định.
        return value.date() if isinstance(value, datetime) else value


class ConversationSession(BaseDocument):
    """Bản nén của MỘT NGÀY đã qua (Task 14, spec mục 4 "Lần trước"). Khác
    `ConversationState` (chỉ giữ ngày HÔM NAY, bị ghi đè khi sang ngày mới):
    đây là 1 doc/user/ngày, sống riêng ngoài `conversations.state`, để "Lần
    trước" còn đọc được sau khi ngày đã qua. Ghi lúc `_compact` nén thành
    công (upsert theo khoá `(user_id, day)`); TTL 30 ngày qua `expires_at`.

    `closed_at` CỐ Ý không được đặt ở phiên bản này (xem ghi chú trong
    `_set_today_fields`) — không rule "Lần trước" nào cần nó.
    """

    user_id: str
    day: date
    digest: Optional[StateDigest] = None
    slots: Optional[ConversationSlots] = None
    closed_at: Optional[datetime] = None
    expires_at: datetime

    @field_validator("day", mode="before")
    @classmethod
    def _datetime_to_date(cls, value):
        # Cùng lý do với ConversationState._datetime_to_date: Mongo không có
        # kiểu date riêng, đọc lên phải tự ép lại.
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
    # Không còn ghi (M3): cờ chờ xác nhận nay là đề xuất trong `proposals`
    # (ProposalService). Giữ field để document cũ vẫn đọc được.
    pending_confirmation: Optional[Dict[str, Any]] = None
    state: Optional[ConversationState] = None
