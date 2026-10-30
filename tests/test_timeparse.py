import inspect
import re
from datetime import date, datetime, timedelta

import pytest

from app.agents.booking_graph import timeparse
from app.agents.booking_graph.timeparse import (
    ParsedTime,
    _guard,
    _to_24h,
    apply_anchor,
    apply_session_day,
    parse_vi_time,
)
from app.core.clock import TZ
from app.models.shop import ShopHours

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều
HOURS = ShopHours(open_time="08:00", close_time="19:00")
ANCHOR_MORNING = datetime(2026, 9, 15, 9, 0, tzinfo=TZ)
ANCHOR_AFTERNOON = datetime(2026, 9, 15, 15, 0, tzinfo=TZ)


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


class TestGuard:
    def test_lets_a_sensible_time_through(self):
        result = _guard(ParsedTime(start_at=at(8, 15)), NOW)
        assert result.start_at == at(8, 15)

    def test_attaches_vietnam_time_when_the_model_forgets_the_offset(self):
        naive = datetime(2026, 8, 8, 15, 0)
        result = _guard(ParsedTime(start_at=naive), NOW)
        assert result.start_at is not None
        assert result.start_at.utcoffset() is not None
        assert result.start_at == at(8, 15)

    def test_a_time_already_past_is_rejected(self):
        result = _guard(ParsedTime(start_at=at(7, 10)), NOW)
        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]

    def test_a_year_off_date_is_rejected(self):
        result = _guard(
            ParsedTime(start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ)), NOW
        )
        assert result.start_at is None

    def test_a_date_too_far_ahead_is_rejected(self):
        far = NOW + timedelta(days=120)
        result = _guard(ParsedTime(start_at=far), NOW)
        assert result.start_at is None
        assert result.missing == ["ngày gần hơn"]

    def test_ninety_days_ahead_is_still_allowed(self):
        edge = NOW + timedelta(days=89)
        assert _guard(ParsedTime(start_at=edge), NOW).start_at == edge

    def test_missing_information_passes_through_untouched(self):
        candidate = ParsedTime(
            start_at=None, partial_date=date(2026, 8, 8), missing=["giờ cụ thể"]
        )
        result = _guard(candidate, NOW)
        assert result.missing == ["giờ cụ thể"]
        assert result.partial_date == date(2026, 8, 8)

    def test_guard_does_not_check_opening_hours(self):
        three_am = _guard(ParsedTime(start_at=at(8, 3)), NOW)
        assert three_am.start_at == at(8, 3)


class TestTo24h:
    @pytest.mark.parametrize(
        "hour,period,expected",
        [
            (3, "chiều", 15),
            (15, "chiều", 15),
            (12, "trưa", 12),
            (12, "chiều", 12),
            (1, "trưa", 13),
            (13, "trưa", 13),
            (9, "sáng", 9),
            (7, "tối", 19),
            (19, "tối", 19),
            (15, None, 15),
            (0, None, 0),
            (3, None, None),
            (9, None, None),
        ],
    )
    def test_converts_12h_and_24h_correctly(self, hour, period, expected):
        assert _to_24h(hour, period) == expected


class TestParseViTimeStructured:
    async def test_full_day_and_hour_with_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=3, period="chiều")
        assert out.start_at == at(8, 15)
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == []
        assert out.day_from == "explicit"
        assert out.source == "code"

    async def test_full_day_and_24h_hour_with_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=15, minute=30, period="chiều")
        assert out.start_at == at(8, 15, 30)
        assert out.missing == []

    async def test_day_only_asks_for_specific_hour(self):
        out = await parse_vi_time(NOW, day="2026-08-08", period="sáng")
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == ["giờ cụ thể"]
        assert out.day_from == "explicit"

    async def test_day_and_ambiguous_hour_asks_for_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=3)
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.partial_hour == 3
        assert out.missing == ["sáng hay chiều"]

    async def test_hour_with_period_without_day_asks_for_day(self):
        out = await parse_vi_time(NOW, hour=3, period="chiều")
        assert out.start_at is None
        assert out.partial_hour == 15
        assert out.missing == ["ngày nào"]

    async def test_ambiguous_hour_without_day_asks_for_day_and_period(self):
        out = await parse_vi_time(NOW, hour=3)
        assert out.start_at is None
        assert out.partial_hour == 3
        assert out.missing == ["ngày nào", "sáng hay chiều"]

    async def test_ambiguous_week_asks_which_week(self):
        out = await parse_vi_time(NOW, ambiguous_week=True, hour=3, period="chiều")
        assert out.start_at is None
        assert out.missing == ["tuần này hay tuần sau"]

    async def test_ambiguous_week_with_ambiguous_hour_keeps_both_questions(self):
        out = await parse_vi_time(NOW, ambiguous_week=True, hour=3, anchor=ANCHOR_MORNING, hours=HOURS)
        assert out.start_at is None
        assert out.missing == ["tuần này hay tuần sau", "sáng hay chiều"]

    async def test_is_now_bumps_to_next_15_minute_slot(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        out = await parse_vi_time(now, is_now=True)
        assert out.start_at == at(7, 14, 45)
        assert out.missing == []
        assert out.day_from == "explicit"

    async def test_empty_call_asks_for_specific_time(self):
        out = await parse_vi_time(NOW)
        assert out.start_at is None
        assert out.missing == ["giờ cụ thể"]

    async def test_out_of_range_hour_or_minute_fails_soft(self):
        out = await parse_vi_time(NOW, hour=25, anchor=ANCHOR_MORNING, hours=HOURS)
        assert out.start_at is None
        assert out.missing == ["giờ cụ thể"]

        out_min = await parse_vi_time(NOW, day="2026-08-08", hour=10, minute=75)
        assert out_min.start_at is None
        assert out_min.partial_date == date(2026, 8, 8)
        assert out_min.missing == ["giờ cụ thể"]


class TestRightNow:
    def test_right_now_becomes_the_next_slot_instead_of_being_called_past(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at == at(7, 14, 45)
        assert result.missing == []

    def test_a_time_inside_the_current_slot_also_moves_up(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=at(7, 14, 30)), now)
        assert result.start_at == at(7, 14, 45)

    def test_the_bumped_time_lands_on_a_15_minute_mark(self):
        from app.core.slots import slot_keys_for

        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at is not None
        assert slot_keys_for(result.start_at, 60)

    def test_exactly_on_a_slot_mark_moves_to_the_next_one(self):
        now = datetime(2026, 8, 7, 14, 30, 0, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at == at(7, 14, 45)

    def test_a_genuinely_past_time_is_still_rejected(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=at(7, 10)), now)
        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]


class TestMissingIsAClosedVocabulary:
    def test_rejects_a_question_outside_the_vocabulary(self):
        with pytest.raises(ValueError):
            ParsedTime(missing=["thứ ba tuần sau là ngày nào cụ thể"])

    @pytest.mark.parametrize("question", [
        "giờ cụ thể",
        "sáng hay chiều",
        "ngày nào",
        "tuần này hay tuần sau",
        "ngày khác — giờ đó qua mất rồi",
        "ngày gần hơn",
    ])
    def test_accepts_every_question_the_code_actually_asks(self, question):
        assert ParsedTime(missing=[question]).missing == [question]


class TestKnownDayIsNeverAskedAgain:
    def test_the_day_question_is_dropped_when_the_day_is_known(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 15), missing=["ngày nào"])
        assert parsed.partial_date == date(2026, 9, 15)
        assert "ngày nào" not in parsed.missing

    def test_the_week_question_is_dropped_too(self):
        parsed = ParsedTime(
            partial_date=date(2026, 9, 15), missing=["tuần này hay tuần sau"]
        )
        assert parsed.partial_date == date(2026, 9, 15)
        assert "tuần này hay tuần sau" not in parsed.missing

    def test_dropping_the_day_question_leaves_the_time_question(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 15), missing=["ngày nào"])
        assert parsed.missing == ["giờ cụ thể"]

    def test_a_known_start_needs_no_question_at_all(self):
        parsed = ParsedTime(
            start_at=datetime(2026, 9, 15, 15, 0, tzinfo=TZ),
            partial_date=date(2026, 9, 15),
            missing=["ngày nào"],
        )
        assert parsed.missing == []

    def test_allows_asking_the_time_when_the_day_is_known(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 17), missing=["giờ cụ thể"])
        assert parsed.partial_date == date(2026, 9, 17)
        assert parsed.missing == ["giờ cụ thể"]

    def test_allows_asking_which_day_when_no_day_is_known(self):
        assert ParsedTime(missing=["ngày nào"]).missing == ["ngày nào"]


class TestApplyAnchorAndSessionDay:
    def test_no_anchor_changes_nothing(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10)
        assert apply_anchor(c, None, HOURS) == c

    def test_missing_day_takes_the_anchor_day_and_marks_day_from_anchor(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10, partial_minute=0)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.missing == []
        assert out.day_from == "anchor"

    def test_missing_period_with_explicit_day_keeps_day_from_explicit(self):
        c = ParsedTime(
            missing=["sáng hay chiều"],
            partial_date=date(2026, 9, 15),
            partial_hour=10,
            day_from="explicit",
        )
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.missing == []
        assert out.day_from == "explicit"

    def test_missing_period_afternoon_anchor(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=4)
        out = apply_anchor(c, ANCHOR_AFTERNOON, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 16, 0, tzinfo=TZ)

    def test_anchor_period_outside_hours_uses_the_other_period(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=7)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at is None and out.missing == ["sáng hay chiều"]

    def test_anchor_period_outside_hours_but_other_period_open(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=6)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 18, 0, tzinfo=TZ)

    def test_missing_both_day_and_period(self):
        c = ParsedTime(missing=["ngày nào", "sáng hay chiều"], partial_hour=10)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.day_from == "anchor"

    def test_missing_the_hour_itself_cannot_be_resolved(self):
        c = ParsedTime(missing=["giờ cụ thể"], partial_date=date(2026, 9, 15))
        assert apply_anchor(c, ANCHOR_MORNING, HOURS).start_at is None

    def test_no_hours_given_trusts_the_anchor_period(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=10)
        assert apply_anchor(c, ANCHOR_MORNING, None).start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)

    def test_out_of_range_hour_is_ignored_not_a_crash(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=25)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_out_of_range_minute_is_ignored_not_a_crash(self):
        c = ParsedTime(
            missing=["sáng hay chiều"], partial_date=date(2026, 9, 15),
            partial_hour=10, partial_minute=70,
        )
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_week_unknown_is_not_resolved(self):
        c = ParsedTime(missing=["tuần này hay tuần sau"], partial_hour=10)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_week_unknown_combined_with_period_is_not_resolved(self):
        c = ParsedTime(missing=["tuần này hay tuần sau", "sáng hay chiều"], partial_hour=3)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_session_day_fills_missing_day_when_hour_and_period_are_known(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10, partial_minute=15)
        out = apply_session_day(c, date(2026, 8, 8))
        assert out.start_at == at(8, 10, 15)
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == []
        assert out.day_from == "anchor"

    def test_session_day_fills_partial_date_when_period_still_missing(self):
        c = ParsedTime(missing=["ngày nào", "sáng hay chiều"], partial_hour=10)
        out = apply_session_day(c, date(2026, 8, 8))
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == ["sáng hay chiều"]
        assert out.day_from == "anchor"


def test_timeparse_uses_zero_regex_and_zero_llm():
    source = inspect.getsource(timeparse)
    assert len(re.findall(r"re\.compile\(", source)) == 0
    assert "build_chat_model" not in source
