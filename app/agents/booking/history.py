"""Lịch sử model đọc từ thread checkpointer — cùng cửa sổ với A.

Thread `<user>:<ngày VN>` chỉ chứa câu khách và câu đáp cuối (tool exchange ở lại
trong agent con). Mỗi message mang mốc `created_at` (`stamp`), CÙNG mốc với bản
ghi trong `conversations` (`now_ms()`): `covers_until` của state nén đọc từ
`conversations` phải loại đúng những tin đó khỏi thread.

Cắt lúc ĐỌC, không lúc ghi: thread chỉ là kho; ngày, 30 phút qua nửa đêm, ngân
sách token và mốc nén đều áp ở đây bằng đúng thuật toán của
`ConversationService.history` (`select_window` + `session_cutoff`), không có bản
thứ hai.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage

from app.core.clock import now_utc
from app.services.conversation import DEFAULT_TOKEN_BUDGET, select_window, session_cutoff

CREATED_AT_KEY = "created_at"


def stamp(message: BaseMessage, at: datetime) -> BaseMessage:
    """Gắn mốc ghi (ISO) vào `response_metadata` — phần không gửi lên model."""
    message.response_metadata = {**(message.response_metadata or {}),
                                 CREATED_AT_KEY: at.isoformat()}
    return message


def stamped_at(message: BaseMessage) -> Optional[datetime]:
    raw = (getattr(message, "response_metadata", None) or {}).get(CREATED_AT_KEY)
    return datetime.fromisoformat(raw) if raw else None


@dataclass
class _Item:
    """Bộ chuyển message → dạng `select_window` đọc (role/content/created_at)."""
    role: str
    content: str
    created_at: datetime
    message: AnyMessage


def _turn_messages(messages: List[AnyMessage]) -> List[AnyMessage]:
    """Chỉ câu khách và câu đáp cuối; bỏ câu khách KHÔNG có câu đáp ngay sau.

    A chỉ ghi `conversations` khi lượt có câu đáp — lượt lỗi hay bị huỷ không
    để lại gì. Thread thì đã nhận câu khách lúc vào lượt; lượt hỏng để lại câu
    hỏi mồ côi. Bỏ ở đây để model thấy đúng như A.
    """
    turns = [m for m in messages
             if isinstance(m, HumanMessage)
             or (isinstance(m, AIMessage) and not m.tool_calls)]
    return [m for i, m in enumerate(turns)
            if not isinstance(m, HumanMessage)
            or (i + 1 < len(turns) and isinstance(turns[i + 1], AIMessage))]


def model_history(
    messages: List[AnyMessage], *, after: Optional[datetime],
    now: Optional[datetime] = None, token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> List[AnyMessage]:
    """Phần lịch sử (KHÔNG gồm câu khách lượt này) được gửi model.

    Tin chưa có stamp (state cũ, test) coi như vừa ghi để không bị cắt.
    """
    now = now or now_utc()
    items = [
        _Item("user" if isinstance(m, HumanMessage) else "assistant",
              m.content if isinstance(m.content, str) else str(m.content),
              stamped_at(m) or now, m)
        for m in _turn_messages(messages)
    ]
    window = select_window(items, cutoff=session_cutoff(now), after=after,
                           token_budget=token_budget)
    return [item.message for item in window]
