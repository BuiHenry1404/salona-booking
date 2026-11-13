"""Tool `parse_time` dùng chung cho agent Đặt lịch và Thông tin tiệm (spec
§3.1, §4.1): agent con điền các trường CÓ CẤU TRÚC nó đọc được từ câu khách,
CODE quy ra ngày giờ (`shared/timeparse.py::parse_vi_time`) — model không bao
giờ tự tính ngày, Orchestrator không đọc giờ.

Quy ước thứ trong tuần: 0 = Thứ Hai .. 6 = Chủ Nhật — cùng quy ước với
`text_scan.scan_weekdays` (veto/guard so lại được), KHÔNG phải quy ước
`ShopHours.closed_days` (0 = Chủ Nhật).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal, Optional

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from app.agents.salon.shared.text_scan import scan_times
from app.agents.salon.shared.timeparse import parse_vi_time
from app.core.clock import now_utc, to_local
from app.core.text import strip_diacritics
from app.core.vi_format import format_vi_datetime

_ALLOWED_PERIODS = frozenset({"sang", "trua", "chieu", "toi"})


def period_from_customer(question: str, hour: int) -> Optional[str]:
    """Part of day the customer typed for `hour` in `question`, when exactly
    one distinct period is said for that hour — else `None` (missing, or
    conflicting, so `parse_vi_time` keeps asking instead of guessing)."""
    periods = {
        bare for h, period, _minute in scan_times(question)
        if h == hour and period is not None
        for bare in [strip_diacritics(period)]
        if bare in _ALLOWED_PERIODS
    }
    if len(periods) == 1:
        return next(iter(periods))
    return None


# `ParsedTime.missing` (chữ tiếng Việt, dữ liệu của timeparse) → mã tiếng Anh
# cùng tên với `QuestionField` để agent con điền `question_field` không cần dịch.
_MISSING_CODES = {
    "giờ cụ thể": "time",
    "sáng hay chiều": "part_of_day",
    "ngày nào": "day",
    "tuần này hay tuần sau": "week",
    "ngày khác — giờ đó qua mất rồi": "time_passed",
    "ngày gần hơn": "too_far",
}


def resolve_day(
    today: date, *, day: Optional[str], weekday: Optional[int], day_offset: Optional[int],
    next_week: Optional[bool],
) -> tuple[Optional[str], bool]:
    """(ngày YYYY-MM-DD hoặc None, tuần còn mơ hồ không). Ưu tiên: `day` >
    `day_offset` > `weekday`.

    Thứ không nói tuần: thứ đó của tuần này đã qua → chắc chắn tuần sau; còn
    tới (kể cả hôm nay) → mơ hồ, để `parse_vi_time` hỏi "tuần này hay tuần
    sau" thay vì đoán (hành vi `ambiguous_week` của gói cũ)."""
    if day:
        return day, False
    if day_offset is not None:
        return (today + timedelta(days=day_offset)).isoformat(), False
    if weekday is None or not 0 <= weekday <= 6:
        return None, False
    this_week = today + timedelta(days=weekday - today.weekday())
    if next_week is True:
        return (this_week + timedelta(days=7)).isoformat(), False
    if next_week is False:
        return this_week.isoformat(), False
    if this_week < today:
        return (this_week + timedelta(days=7)).isoformat(), False
    return None, True


@tool
async def parse_time(
    day: Optional[str] = None,
    weekday: Optional[int] = None,
    day_offset: Optional[int] = None,
    next_week: Optional[bool] = None,
    hour: Optional[int] = None,
    minute: Optional[int] = None,
    part_of_day: Optional[Literal["sang", "trua", "chieu", "toi"]] = None,
    runtime: ToolRuntime[Any] = None,
) -> str:
    """Turn what the customer said about a time into an exact moment. Call it
    for EVERY time the customer mentions; never compute a date or an ISO
    string yourself. Fill only what the customer actually said:
    - `day`: YYYY-MM-DD copied from the 14-day calendar in the context block.
    - `day_offset`: days from today (0 = today, 1 = tomorrow, 2 = the day after).
    - `weekday`: 0 = Monday ... 6 = Sunday. `next_week`: true if they said next
      week, false if they said this week, null if they did not say.
    - `hour`: the clock hour as said (1-12 with `part_of_day`, or 0-23).
    - `minute`: 0-59 (null = on the hour). `part_of_day`: morning "sang",
      noon "trua", afternoon "chieu", evening "toi" — only if they said it.
    Result: {"iso", "spoken"} — copy `iso` unchanged into other tools and use
    `spoken` when you describe the time. Or {"missing": [...]} with codes
    "day", "week", "time", "part_of_day", "time_passed", "too_far" (and `day`
    when the date is known): that piece must be asked from the customer."""
    if part_of_day is None and hour is not None and 1 <= hour <= 12 and runtime is not None:
        question = getattr(runtime.context, "question", "") or ""
        part_of_day = period_from_customer(question, hour)
    now = now_utc()
    resolved_day, ambiguous_week = resolve_day(
        to_local(now).date(), day=day, weekday=weekday, day_offset=day_offset,
        next_week=next_week,
    )
    parsed = await parse_vi_time(
        now, day=resolved_day, hour=hour, minute=minute or 0, part_of_day=part_of_day,
        ambiguous_week=ambiguous_week,
    )
    if parsed.start_at is not None:
        local = to_local(parsed.start_at)
        return json.dumps({"iso": local.isoformat(), "spoken": format_vi_datetime(local)},
                          ensure_ascii=False)
    payload: dict = {"missing": [_MISSING_CODES[piece] for piece in parsed.missing]}
    if parsed.partial_date is not None:
        payload["day"] = parsed.partial_date.isoformat()
    return json.dumps(payload, ensure_ascii=False)
