"""Gọi Azure OpenAI thật. Mặc định bị loại khỏi pytest bởi addopts trong
`[tool.pytest.ini_options]` của pyproject.toml. Chạy tay bằng:
pytest tests/test_timeparse_llm.py -m llm -v

Đo xem Booking Agent (với Lịch 14 ngày trong context_block) trích xuất tham số
cho `parse_time` và giải ra `ParsedTime` có chính xác với tiếng Việt thật không.
"""
from datetime import datetime
from typing import Optional

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agents.booking.context import format_14_day_calendar, format_vi_datetime
from app.agents.booking.prompts.appointment import APPOINTMENT_PROMPT
from app.agents.booking.timeparse import ParsedTime, parse_vi_time
from app.agents.llm import build_chat_model
from app.core.clock import TZ
from app.models.shop import ShopHours

pytestmark = [pytest.mark.llm, pytest.mark.asyncio]

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều
HOURS = ShopHours(open_time="08:00", close_time="19:00")


async def _llm_parse(text: str) -> ParsedTime:
    @tool
    async def parse_time(
        day: Optional[str] = None,
        hour: Optional[int] = None,
        minute: int = 0,
        period: Optional[str] = None,
        is_now: bool = False,
        ambiguous_week: bool = False,
        anchor: Optional[str] = None,
    ) -> str:
        """Turn what the customer said about time into a concrete date and time."""
        return "{}"

    ctx = (
        f"Bây giờ là {format_vi_datetime(NOW)} (hôm nay là {NOW.date().isoformat()}, giờ Việt Nam).\n"
        f"{format_14_day_calendar(NOW.date())}\n"
        "Bạn đang nói chuyện với: Lan.\nGọi khách là: chị Lan."
    )
    model = build_chat_model(tags=["timeparse"], streaming=False).bind_tools([parse_time])
    ai = await model.ainvoke([
        SystemMessage(content=APPOINTMENT_PROMPT),
        HumanMessage(content=f"{ctx}\n\nKhách nói: {text}"),
    ])
    if not ai.tool_calls:
        return ParsedTime(missing=[])
    args = ai.tool_calls[0]["args"]
    return await parse_vi_time(
        NOW,
        day=args.get("day"),
        hour=args.get("hour"),
        minute=args.get("minute") or 0,
        period=args.get("period"),
        is_now=bool(args.get("is_now")),
        ambiguous_week=bool(args.get("ambiguous_week")),
        hours=HOURS,
    )


@pytest.mark.parametrize(
    "text,expected_iso",
    [
        ("thứ Bảy này 2 giờ chiều", "2026-08-08T14:00:00+07:00"),
        ("thứ Hai tuần sau lúc 9 giờ sáng", "2026-08-10T09:00:00+07:00"),
        ("ngày 10/8 lúc 4 giờ chiều", "2026-08-10T16:00:00+07:00"),
        ("trưa mai 12 giờ", "2026-08-08T12:00:00+07:00"),
        ("chiều mai lúc 5 giờ", "2026-08-08T17:00:00+07:00"),
    ],
)
async def test_understands_real_vietnamese(text, expected_iso):
    result = await _llm_parse(text)
    assert result.start_at is not None, f"không đọc được: {text}"
    assert result.start_at.isoformat() == expected_iso


@pytest.mark.parametrize(
    "text,phai_hoi_lai",
    [
        ("thứ Năm", "tuần này hay tuần sau"),
        ("sáng mai", "giờ cụ thể"),
        ("3 giờ", "sáng hay chiều"),
        ("khi nào cũng được", "chưa có gì cụ thể"),
    ],
)
async def test_asks_back_instead_of_guessing(text, phai_hoi_lai):
    result = await _llm_parse(text)
    assert result.start_at is None, f"không được đoán với: {text}"
    assert result.missing, f"phải nói rõ thiếu gì ({phai_hoi_lai}) với: {text}"


async def test_a_question_with_no_time_returns_nothing():
    result = await _llm_parse("chủ tiệm rảnh không")
    assert result.start_at is None


@pytest.mark.parametrize(
    "text",
    [
        "bây giờ",
        "giờ chị qua được không",
        "chị qua liền được không",
        "giờ này qua được không",
    ],
)
async def test_right_now_resolves_to_the_next_slot(text):
    result = await _llm_parse(text)
    assert result.start_at is not None, f"vẫn hỏi lại thay vì hiểu: {text} ({result.missing})"
    assert result.start_at.isoformat() == "2026-08-07T14:45:00+07:00"
