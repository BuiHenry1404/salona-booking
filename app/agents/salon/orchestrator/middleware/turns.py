"""Ranh giới lượt trong `messages` của thread (spec §3.4, §3.5) — dùng chung
cho đầu lượt, bối cảnh và guard, để ba nơi hiểu "câu khách" / "lượt" một kiểu.

Thread giữ ĐỦ lịch sử cả cuộc trò chuyện; một lượt bắt đầu ở một HumanMessage
**mở lượt**: câu khách gõ, hoặc HumanMessage do code dựng cho lượt nút bấm
(`additional_kwargs["salon_button"]`). Hai loại HumanMessage KHÔNG mở lượt:
lời nhắc của guard (`GUARD_HINT_MARK`) và bản tóm tắt của
`SummarizationMiddleware` (`lc_source == "summarization"`).
"""
from __future__ import annotations

from typing import Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# Khoá `additional_kwargs` trên HumanMessage nhắc lỗi của guard; giá trị là
# danh sách mã từ chối (đọc lại ở `ContextMiddleware` để chọn tool cho lần viết lại).
GUARD_HINT_MARK = "salon_guard_hint"
BUTTON_MARK = "salon_button"
SUMMARY_SOURCE = "summarization"


def is_guard_hint(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and GUARD_HINT_MARK in message.additional_kwargs


def is_summary(message: BaseMessage) -> bool:
    return (isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == SUMMARY_SOURCE)


def is_button(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(BUTTON_MARK))


def opens_turn(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and not is_guard_hint(message) and not is_summary(message)


def is_customer_text(message: BaseMessage) -> bool:
    """Chữ khách gõ — luôn bọc nhãn untrusted khi gửi model, là `question` của guard."""
    return opens_turn(message) and not is_button(message)


def turn_openers(messages: Sequence[BaseMessage]) -> list[int]:
    return [i for i, message in enumerate(messages) if opens_turn(message)]


def current_turn_start(messages: Sequence[BaseMessage]) -> int:
    """Vị trí message mở lượt hiện tại; không có thì 0 (cả thread là lượt này)."""
    openers = turn_openers(messages)
    return openers[-1] if openers else 0


def final_reply(segment: Sequence[BaseMessage]) -> Optional[AIMessage]:
    """Câu trả lời cuối của một lượt: AIMessage KHÔNG tool call cuối cùng.
    Lượt bị cắt sau một ToolMessage thì không có (không lấy AIMessage có tool
    call — gửi nó mà thiếu ToolMessage là lỗi 400, bẫy 13)."""
    for message in reversed(segment):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message
    return None


def previous_segments(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    """Các lượt trước lượt hiện tại, mỗi lượt bắt đầu ở message mở lượt của nó."""
    openers = turn_openers(messages)
    return [list(messages[a:b]) for a, b in zip(openers, openers[1:])]


def previous_replies(messages: Sequence[BaseMessage]) -> list[str]:
    """Câu trả lời cuối của các lượt trước (guard kiểm lặp câu)."""
    replies = []
    for segment in previous_segments(messages):
        reply = final_reply(segment)
        if reply is not None and reply.text:
            replies.append(reply.text)
    return replies


def current_question(messages: Sequence[BaseMessage]) -> str:
    """Chữ khách của lượt hiện tại; lượt nút bấm (message do code dựng) → ""."""
    openers = turn_openers(messages)
    if not openers or not is_customer_text(messages[openers[-1]]):
        return ""
    return messages[openers[-1]].text


def hint_codes(messages: Sequence[BaseMessage]) -> list[str]:
    """Mã từ chối của lời nhắc guard mới nhất (rỗng nếu không có)."""
    for message in reversed(messages):
        if is_guard_hint(message):
            return list(message.additional_kwargs[GUARD_HINT_MARK])
    return []
