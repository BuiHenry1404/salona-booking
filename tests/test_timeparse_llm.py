"""Gọi Azure OpenAI thật. Mặc định bị loại khỏi pytest bởi addopts trong
`[tool.pytest.ini_options]` của pyproject.toml. Chạy tay bằng:
pytest tests/test_timeparse_llm.py -m llm -v

Đây là chỗ đo xem prompt có thật sự hiểu tiếng Việt hay không. Sửa prompt xong
phải chạy lại file này trước khi tin là đã tốt hơn.
"""
from datetime import datetime

import pytest

from app.agents.booking_graph.timeparse import parse_vi_time
from app.core.clock import TZ

pytestmark = [pytest.mark.llm, pytest.mark.asyncio]

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều


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
    result = await parse_vi_time(text, NOW, timeout=10.0)
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
    """Đoán thay khách là kiểu hỏng tệ nhất: cụ già tới tiệm lúc đóng cửa."""
    result = await parse_vi_time(text, NOW, timeout=10.0)
    assert result.start_at is None, f"không được đoán với: {text}"
    assert result.missing, f"phải nói rõ thiếu gì ({phai_hoi_lai}) với: {text}"


async def test_a_question_with_no_time_returns_nothing():
    result = await parse_vi_time("chủ tiệm rảnh không con", NOW, timeout=10.0)
    assert result.start_at is None


@pytest.mark.parametrize(
    "text",
    [
        "bây giờ",
        "giờ cô qua được không",
        "cô qua liền được không con",
        "giờ này qua được không",
    ],
)
async def test_right_now_resolves_to_the_next_slot(text):
    """Khách tiệm vãng lai nói "qua liền" suốt. Trước đây prompt không có luật
    nào cho "bây giờ" nên model trả null và lễ tân hỏi lại "mấy giờ ạ?" — câu
    hỏi vô nghĩa với người vừa nói là muốn tới ngay."""
    result = await parse_vi_time(text, NOW, timeout=10.0)

    assert result.start_at is not None, f"vẫn hỏi lại thay vì hiểu: {text} ({result.missing})"
    # NOW là 14:30:00 đúng mốc, nên mốc kế tiếp là 14:45.
    assert result.start_at.isoformat() == "2026-08-07T14:45:00+07:00"
