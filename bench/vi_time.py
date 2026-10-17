"""Cách người Việt đọc giờ và ngày — dùng để kiểm câu trả lời của bot trong
benchmark có nhắc đúng giờ/ngày mà khách mong đợi hay không.

Hộp đen: module này KHÔNG import `app`. Luật đọc giờ lặp lại y hệt
`_clock_phrase` ở `app/agents/booking_graph/context.py` (đọc để khớp chữ,
không import) — lệch một chữ là bot và benchmark "nghe" ra hai giọng khác
nhau trong cùng một câu.
"""

from __future__ import annotations

import re
from datetime import datetime


def clock_phrase(dt: datetime) -> str:
    """'3 giờ chiều', '9 giờ rưỡi sáng', '1 giờ 45 chiều'.

    0h -> "12 giờ đêm"; 12h -> "12 giờ trưa" (không phải "chiều" — đó là lỗi
    đã sửa trong app, xem CONTEXT.md); phút 30 -> "rưỡi"; phút khác -> "giờ MM".
    """
    hour, minute = dt.hour, dt.minute

    if hour == 0:
        period, display = "đêm", 12
    elif hour < 12:
        period, display = "sáng", hour
    elif hour == 12:
        period, display = "trưa", 12
    elif hour < 18:
        period, display = "chiều", hour - 12
    else:
        period, display = "tối", hour - 12

    if minute == 0:
        clock = f"{display} giờ"
    elif minute == 30:
        clock = f"{display} giờ rưỡi"
    else:
        clock = f"{display} giờ {minute:02d}"
    return f"{clock} {period}"


def day_phrase(dt: datetime) -> str:
    """'2026-10-05' -> '5/10' (không số 0 đứng trước, không có năm)."""
    return f"{dt.day}/{dt.month}"


def mentions(answer: str, at: datetime) -> bool:
    """Câu trả lời `answer` có nhắc cả ngày lẫn giờ của `at` hay không.

    So chữ thường. Ngày phải có biên số ở cả hai phía — "5/10" không được
    khớp bên trong "15/10" hay "5/100".
    """
    text = answer.lower()
    day = day_phrase(at).lower()
    clock = clock_phrase(at).lower()

    day_pattern = r"(?<!\d)" + re.escape(day) + r"(?!\d)"
    return bool(re.search(day_pattern, text)) and clock in text
