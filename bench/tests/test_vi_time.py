"""Test cách đọc giờ/ngày kiểu Việt, dùng để chấm câu trả lời của bot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bench.vi_time import clock_phrase, day_phrase, mentions

VN_TZ = timezone(timedelta(hours=7))


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (15, 0, "3 giờ chiều"),
        (9, 30, "9 giờ rưỡi sáng"),
        (12, 0, "12 giờ trưa"),
        (0, 0, "12 giờ đêm"),
        (13, 45, "1 giờ 45 chiều"),
    ],
)
def test_clock_phrase(hour, minute, expected):
    dt = datetime(2026, 10, 17, hour, minute, tzinfo=VN_TZ)
    assert clock_phrase(dt) == expected


def test_mentions_needs_day_and_clock():
    at = datetime(2026, 10, 5, 15, 0, tzinfo=VN_TZ)
    assert mentions("Dạ anh/chị có lịch 5/10, 3 giờ chiều ạ", at)
    # Chỉ có giờ, thiếu ngày -> không khớp.
    assert not mentions("Dạ anh/chị có lịch lúc 3 giờ chiều ạ", at)
    # Chỉ có ngày, thiếu giờ -> không khớp.
    assert not mentions("Dạ anh/chị có lịch ngày 5/10 ạ", at)


def test_day_inside_longer_date_does_not_match():
    at = datetime(2026, 10, 5, 15, 0, tzinfo=VN_TZ)
    assert not mentions("Thứ Năm 15/10, 3 giờ chiều", at)


def test_day_phrase():
    dt = datetime(2026, 10, 5, 9, 0, tzinfo=VN_TZ)
    assert day_phrase(dt) == "5/10"
