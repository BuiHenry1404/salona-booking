"""Giờ-trong-ngày dùng chung cho `guard/` (Task 1, tách khỏi `facts.py` để
`catalog.py` dùng lại không phải import cả `facts.py`).

Giờ so theo GIÁ TRỊ TRONG NGÀY, không theo nhãn buổi (review Task 12 #6):
dữ kiện là `ClockKey = (giờ 0..23, phút, nhãn buổi không dấu)` — vd 15:00 ->
`(15, 0, "chieu")`, nhãn chỉ để đọc log; giờ trong câu (bot hay khách) là tập
các giá trị 24h `(giờ, phút)` mà cách nói đó có thể mang (`readings`):
"6 giờ chiều" và "6 giờ tối" cùng là 18:00, "11 giờ trưa" là 11:00, "3 giờ"
chưa kèm buổi là {03:00, 15:00}, "3 giờ sáng" chỉ là 03:00. Bài học Task 8:
`scan_times` trả buổi CÓ DẤU ("tối") — mọi buổi qua `strip_diacritics`
(`_bare`) trước khi tra bảng `_PERIOD_HOURS`.

Không regex (gói `salon` không `import re`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from app.agents.salon.shared.schemas import Moment
from app.agents.salon.shared.text_scan import scan_spoken_clocks, scan_times
from app.core.clock import to_local
from app.core.text import strip_diacritics
from app.core.vi_format import format_vi_hhmm

ClockKey = tuple[int, int, Optional[str]]
Reading = frozenset[tuple[int, int]]

# Giờ đọc 1..12 + buổi -> giờ trong ngày, theo cách người Việt nói (cả cách
# chồng lấn: "6 giờ chiều" = "6 giờ tối" = 18:00, "1 giờ trưa" = "1 giờ chiều").
# Một cặp không có ở đây ("3 giờ tối") không mang giờ nào -> không khớp dữ kiện.
_PERIOD_HOURS: dict[str, dict[int, int]] = {
    "sang": {h: h for h in range(1, 12)},
    "trua": {11: 11, 12: 12, 1: 13, 2: 14},
    "chieu": {12: 12, **{h: h + 12 for h in range(1, 8)}},
    "toi": {12: 0, **{h: h + 12 for h in range(5, 12)}},
    "dem": {12: 0, **{h: h for h in range(1, 5)}, **{h: h + 12 for h in range(9, 12)}},
}


def _bare(period: Optional[str]) -> Optional[str]:
    return strip_diacritics(period) if period else None


def clock_of_24h(hour: int, minute: int) -> ClockKey:
    """15:00 -> (15, 0, "chieu"); 00:30 -> (0, 30, "dem"). Nhãn buổi lấy từ
    `format_vi_hhmm` — cùng một nguồn với cách bot đọc giờ cho khách."""
    ((_, _, period),) = scan_spoken_clocks(format_vi_hhmm(f"{hour:02d}:{minute:02d}"))
    return hour, minute, _bare(period)


def readings(hour: int, period: Optional[str], minute: Optional[int]) -> Reading:
    """Mọi giờ trong ngày mà một cách nói `(hour, period, minute)` có thể mang.
    Giờ 24h ("15h", "15:00", "15 giờ chiều") là chính nó; 1..12 chưa kèm buổi
    là hai cách đọc; kèm buổi thì tra `_PERIOD_HOURS`."""
    minute = minute or 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return frozenset()
    if hour == 0 or hour > 12:
        return frozenset({(hour, minute)})
    bare = _bare(period)
    if bare is None:
        return frozenset({(hour % 12, minute), (hour % 12 + 12, minute)})
    hour24 = _PERIOD_HOURS.get(bare, {}).get(hour)
    return frozenset() if hour24 is None else frozenset({(hour24, minute)})


def clocks_in_text(text: str) -> list[Reading]:
    """Mọi giờ trong một câu (câu bot hay câu khách), mỗi giờ là tập cách đọc."""
    return [readings(hour, period, minute) for hour, period, minute in scan_times(text or "")]


def is_known(said: Reading, known: Iterable[ClockKey]) -> bool:
    return any(key[:2] in said for key in known)


def clocks_of_spoken(spoken: Optional[str]) -> set[ClockKey]:
    clocks: set[ClockKey] = set()
    for hour, minute, period in scan_spoken_clocks(spoken or ""):
        clocks |= {clock_of_24h(h, m) for h, m in readings(hour, period, minute)}
    return clocks


def clocks_of_moment(moment: Optional[Moment]) -> set[ClockKey]:
    if moment is None:
        return set()
    clocks = clocks_of_spoken(moment.spoken)
    try:
        local = to_local(datetime.fromisoformat(moment.iso))
        clocks.add(clock_of_24h(local.hour, local.minute))
    except (TypeError, ValueError):
        pass
    return clocks


def clocks_of_hhmm(hhmm: str) -> set[ClockKey]:
    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
        return {clock_of_24h(hour, minute)}
    except ValueError:
        return set()


def customer_clocks(question: str) -> set[ClockKey]:
    """Giờ khách vừa nói — bot được nhắc lại. Chưa nói buổi thì cả hai cách
    đọc đều tính (khớp cả khi bot hỏi lại "sáng hay chiều")."""
    return {clock_of_24h(h, m) for said in clocks_in_text(question) for h, m in said}
