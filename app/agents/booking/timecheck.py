"""Lưới CODE so giờ/ngày/thứ trong câu khách với một đề xuất — không từ điển ý
định, chỉ quét bằng `tokens.scan_*`. Dời nguyên văn từ `reply.py` của gói cũ ở
Task 6.1 (`other_time`, `mentions_other_time`, `restates_proposal_time`,
`spoken_times` và helper riêng của chúng); phần quyết nhãn (`decide`,
`resolve_label`) ở lại gói cũ.
"""
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from app.agents.booking import tokens
from app.agents.booking.timeparse import _to_24h
from app.core.clock import to_local

_PERIOD_FROM_BARE = {"sang": "sáng", "trua": "trưa", "chieu": "chiều", "toi": "tối"}


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _hour_differs(raw_hour: int, period: Optional[str], start_hour: int) -> bool:
    hour = _to_24h(raw_hour, period) if period else None
    if hour is not None:
        return hour != start_hour
    return raw_hour % 12 != start_hour % 12


def _structured_time_differs(item: Any, local_start: datetime, local_now: datetime) -> bool:
    get = item.get if isinstance(item, Mapping) else lambda k: getattr(item, k, None)
    hour, minute, part = get("hour"), get("minute"), get("part_of_day")
    weekday, day, month, offset = get("weekday"), get("day"), get("month"), get("day_offset")
    period = _PERIOD_FROM_BARE.get(part) if part else None
    if hour is not None and _hour_differs(hour, period, local_start.hour):
        return True
    if minute is not None and minute != local_start.minute:
        return True
    if offset is not None:
        if offset == -1 or local_now.date().toordinal() + offset != local_start.date().toordinal():
            return True
    if weekday is not None and weekday != local_start.weekday():
        return True
    if day is not None and day != local_start.day:
        return True
    if month is not None and month != local_start.month:
        return True
    return False


def mentions_other_time(text: str, start: datetime, now: datetime,
                        *, mentioned_times: Optional[Sequence[Any]] = None) -> bool:
    """Khách nhắc một giờ, một ngày hay một thứ KHÁC với đề xuất."""
    local_start, local_now = to_local(_as_datetime(start)), to_local(now)
    for hour, period, minute in tokens.scan_times(text):
        if _hour_differs(hour, period, local_start.hour):
            return True
        if minute is not None and minute != local_start.minute:
            return True
    for offset in tokens.scan_day_offsets(text):
        if local_now.date().toordinal() + offset != local_start.date().toordinal():
            return True
    for weekday in tokens.scan_weekdays(text):
        if weekday != local_start.weekday():
            return True
    if tokens.has_next_week(text) and (local_start.date() - local_now.date()).days < 7:
        return True
    for item in mentioned_times or ():
        if _structured_time_differs(item, local_start, local_now):
            return True
    return False


def _has_time(text: str) -> bool:
    return bool(tokens.scan_times(text))


def _without_queued_clauses(lowered: str, start: datetime, queued, now: datetime) -> str:
    """Bỏ những mệnh đề chỉ nói về một lịch ĐANG CHỜ (lỗi live B2 #2)."""
    if not queued:
        return lowered
    waiting = [_as_datetime(q) for q in queued]
    kept = []
    for clause in tokens.split_clauses(lowered, break_words=("còn", "con"), preserve_date_comma=False):
        if (clause.strip() and _has_time(clause)
                and mentions_other_time(clause, start, now)
                and any(not mentions_other_time(clause, q, now) for q in waiting)):
            continue
        kept.append(clause)
    remainder = " , ".join(c.strip() for c in kept if c.strip())
    return remainder if remainder else lowered


def other_time(text: str, proposal: Mapping[str, Any], now: datetime,
               mentioned_times: Sequence[Any] = ()) -> bool:
    """Khách nói một giờ/ngày/thứ KHÁC đề xuất — lưới code, chắc hơn nhãn model.

    Quét chữ sau khi bỏ mệnh đề chỉ nói về lịch đang chờ. Với `mentioned_times`
    (đối tượng `MentionedTime` hay dict từ `model_dump()`): bỏ mục khớp một giờ
    đang chờ, phần còn lại so với `start_at`.
    """
    start = _as_datetime(proposal["start_at"])
    queued = [_as_datetime(q) for q in (proposal.get("queued") or [])]
    lowered = _without_queued_clauses((text or "").strip().lower(), start, queued, now)
    if mentions_other_time(lowered, start, now):
        return True
    local_start, local_now = to_local(start), to_local(now)
    local_queued = [to_local(q) for q in queued]
    for item in mentioned_times or ():
        if any(not _structured_time_differs(item, local_q, local_now) for local_q in local_queued):
            continue
        if _structured_time_differs(item, local_start, local_now):
            return True
    return False


def restates_proposal_time(text: str, proposal: Mapping[str, Any], now: datetime) -> bool:
    """Khách CÓ nói giờ và mọi giờ đều trùng đề xuất (chỉ dựa vào quét chữ).

    Xét trên câu GỐC, không bỏ mệnh đề về lịch đang chờ: "9 giờ sớm quá, 2 giờ
    chiều đi" (đề xuất 9 giờ, chờ 14 giờ) có một giờ khác nên không phải nhắc lại.
    """
    lowered = (text or "").strip().lower()
    return _has_time(lowered) and not mentions_other_time(
        lowered, _as_datetime(proposal["start_at"]), now)


def spoken_times(text: str, proposal: Optional[Mapping[str, Any]] = None,
                 now: Optional[datetime] = None) -> Optional[str]:
    """Các giờ khách nói, viết lại bằng CODE ("4 giờ", "10 giờ 30 sáng").

    Không bao giờ chép chữ khách (chống tiêm lệnh). Không có giờ → None.
    Có `proposal` (+ `now`): bỏ trước các mệnh đề chỉ nói về lịch đang chờ, để
    chỉ còn giờ cho CHÍNH đề xuất này. Giới hạn bộ quét: "3 rưỡi chiều" ra
    "3 giờ 30" — mất buổi.
    """
    if proposal is not None and now is not None:
        text = _without_queued_clauses((text or "").strip().lower(),
                                       _as_datetime(proposal["start_at"]),
                                       proposal.get("queued") or [], now)
    parts = []
    for hour, period, minute in tokens.scan_times(text or ""):
        spoken = f"{hour} giờ"
        if minute:  # minute=0 bị bỏ qua cố ý: "3 giờ" đọc tự nhiên hơn "3 giờ 0"
            spoken = f"{spoken} {minute}"
        if period:
            spoken = f"{spoken} {period}"
        parts.append(spoken)
    return ", ".join(parts) if parts else None
