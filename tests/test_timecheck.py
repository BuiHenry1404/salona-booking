"""Lưới CODE so giờ/ngày/thứ trong câu khách với một đề xuất
(`app.agents.booking.timecheck`): `other_time`, `mentions_other_time`,
`restates_proposal_time`. Dời từ `tests/test_reply_veto.py` ở Task 6.3
(M6-cutover) — phần quyết nhãn (`decide`, `resolve_label`, `spoken_times`)
không còn ở module này (xem `app/agents/booking/reply_brief.py`,
`app/agents/booking/orchestrator/decide.py`), rewrite ở task khác.
"""
from datetime import datetime

import pytest

from app.agents.booking.timecheck import mentions_other_time, other_time, restates_proposal_time
from app.core.clock import TZ

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)            # thứ Hai
START = datetime(2026, 10, 20, 15, 0, tzinfo=TZ)          # mai (thứ Ba) 3 giờ chiều
P_BOOK = {"kind": "book", "start_at": START.isoformat()}


def test_other_time_ignores_clauses_about_the_queued_slot():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("ừ 9 giờ, còn 2 giờ chiều cho cháu thì sao", p, NOW) is False


def test_other_time_still_catches_a_new_time_while_queued():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("ừ nhưng dời qua 10 giờ", p, NOW) is True


def test_other_time_skips_structured_items_matching_the_queue():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("", p, NOW, [{"hour": 2, "part_of_day": "chieu"}]) is False
    assert other_time("", p, NOW, [{"hour": 10}]) is True


def test_other_time_accepts_dict_mentioned_times():
    """`other_time` nhận dict (vd `model_dump()` của một schema thời gian) trong `mentioned_times`."""
    assert other_time("", P_BOOK, NOW, [{"hour": 4, "part_of_day": "chieu"}]) is True
    assert other_time("", P_BOOK, NOW, [{"hour": 3, "part_of_day": "chieu", "day_offset": 1}]) is False


class TestMentionsOtherTime:
    @pytest.mark.parametrize("text", [
        "4 giờ", "3 giờ sáng", "mốt nha", "hôm nay luôn", "ngày kia", "thứ năm",
        "chủ nhật", "tuần sau", "tuần tới", "thu nam", "chu nhat", "tuan sau", "4 gio",
    ])
    def test_other(self, text):
        assert mentions_other_time(text, START, NOW) is True, text

    @pytest.mark.parametrize("text", [
        "3 giờ", "3 giờ chiều", "15h", "mai", "ngày mai", "thứ ba", "thu ba",
        "ừ", "dạ đúng rồi", "3 gio chieu mai",
    ])
    def test_same(self, text):
        assert mentions_other_time(text, START, NOW) is False, text

    def test_next_week_is_fine_when_the_proposal_is_next_week(self):
        far = datetime(2026, 10, 28, 15, 0, tzinfo=TZ)
        assert mentions_other_time("tuần sau nha", far, NOW) is False


def test_mentions_other_time_also_checks_structured_mentioned_times():
    # Kể cả khi text không chứa chữ số (vd viết bằng chữ), mentioned_times vẫn bắt được giờ lệch
    assert mentions_other_time(
        "dạ bốn giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[{"hour": 16, "part_of_day": "chieu"}],
    ) is True
    assert mentions_other_time(
        "dạ ba giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[{"hour": 15, "part_of_day": "chieu"}],
    ) is False


class TestRestatesProposalTime:
    def test_restates_the_exact_proposed_time(self):
        assert restates_proposal_time("ừ 3 giờ chiều mai nha", P_BOOK, NOW) is True

    def test_does_not_restate_when_no_time_is_named(self):
        assert restates_proposal_time("ừ đúng rồi", P_BOOK, NOW) is False

    def test_does_not_restate_when_a_different_time_is_named(self):
        assert restates_proposal_time("ok bốn giờ nha", P_BOOK, NOW) is False
