from datetime import date, datetime, timedelta

import pytest

from app.agents.booking_graph.timeparse import (ParsedTime, _guard,
                                                _match_regex, parse_vi_time)
from app.core.clock import TZ

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


class TestGuard:
    def test_lets_a_sensible_time_through(self):
        result = _guard(ParsedTime(start_at=at(8, 15), source="llm"), NOW)
        assert result.start_at == at(8, 15)

    def test_attaches_vietnam_time_when_the_model_forgets_the_offset(self):
        """LLM rất hay trả '2026-08-08T15:00:00' không kèm offset."""
        naive = datetime(2026, 8, 8, 15, 0)
        result = _guard(ParsedTime(start_at=naive, source="llm"), NOW)

        assert result.start_at is not None
        assert result.start_at.utcoffset() is not None
        assert result.start_at == at(8, 15)

    def test_a_time_already_past_is_rejected(self):
        """'hôm nay 10 giờ sáng' nói lúc 2 giờ chiều là giờ không đặt được."""
        result = _guard(ParsedTime(start_at=at(7, 10), source="regex"), NOW)

        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]

    def test_a_year_off_date_is_rejected(self):
        """Kiểu hỏng nguy hiểm nhất: model không chắc hôm nay là ngày nào nên
        suy từ dữ liệu huấn luyện. '2025-08-08' là chuỗi hợp lệ hoàn toàn."""
        result = _guard(
            ParsedTime(start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ), source="llm"), NOW
        )
        assert result.start_at is None

    def test_a_date_too_far_ahead_is_rejected(self):
        """Không ai đặt lịch nail trước ba tháng."""
        far = NOW + timedelta(days=120)
        result = _guard(ParsedTime(start_at=far, source="llm"), NOW)

        assert result.start_at is None
        assert result.missing == ["ngày gần hơn"]

    def test_ninety_days_ahead_is_still_allowed(self):
        edge = NOW + timedelta(days=89)
        assert _guard(ParsedTime(start_at=edge, source="llm"), NOW).start_at == edge

    def test_missing_information_passes_through_untouched(self):
        candidate = ParsedTime(
            start_at=None, partial_date=date(2026, 8, 8), missing=["giờ cụ thể"], source="llm"
        )
        result = _guard(candidate, NOW)

        assert result.missing == ["giờ cụ thể"]
        assert result.partial_date == date(2026, 8, 8)

    def test_guard_does_not_check_opening_hours(self):
        """Giờ mở cửa do AppointmentService và find_free_slots lo. Kiểm ở đây nữa
        là nó nằm hai chỗ, đổi một chỗ quên chỗ kia."""
        three_am = _guard(ParsedTime(start_at=at(8, 3), source="llm"), NOW)
        assert three_am.start_at == at(8, 3)


class TestRegexMatches:
    """Ca dương: khớp trọn vẹn thì regex trả kết quả, không gọi LLM."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("mai 3h chiều", at(8, 15)),
            ("mai 3 giờ chiều", at(8, 15)),
            ("ngày mai 3g chiều", at(8, 15)),
            ("hôm nay 4h chiều", at(7, 16)),
            ("ngày kia 15h", at(9, 15)),
            ("mốt 9 giờ sáng", at(9, 9)),
            ("mai 8h sáng làm tóc", at(8, 8)),
            ("cho cô đặt mai 7 giờ tối nhé", at(8, 19)),
            ("mai 12 giờ trưa", at(8, 12)),
        ],
    )
    def test_matches(self, text, expected):
        assert _match_regex(text, NOW) == expected


class TestRegexDefers:
    """Ca ÂM — phần quan trọng nhất của cả file.

    Regex chỉ được trả lời khi câu khớp TRỌN VẸN. Ai nới nó ra sau này thì
    chính mấy ca này đỏ lên trước, đúng chỗ cần chặn.
    """

    @pytest.mark.parametrize(
        "text,ly_do",
        [
            ("3h chiều", "thiếu ngày — hôm nay hay mai"),
            ("sáng mai", "thiếu giờ"),
            ("chiều mai", "thiếu giờ"),
            ("mai", "thiếu giờ"),
            ("mai 3h", "thiếu buổi — 3 sáng hay 3 chiều"),
            ("thứ Năm", "tuần này hay tuần sau là phán đoán"),
            ("thứ Năm 3h chiều", "vẫn là phán đoán tuần nào"),
            ("tuần sau", "quá mơ hồ"),
            ("mùng 2", "regex đụng vào là hỏng"),
            ("cuối tuần", "quá mơ hồ"),
            ("ngày 8/8 lúc 3h chiều", "ngày tường minh không nằm trong tập đóng"),
            ("chủ tiệm rảnh không con", "không có gì để parse"),
            ("email cho cô lúc 3h chiều", "'mai' nằm trong 'email', không phải ngày mai"),
        ],
    )
    def test_defers_to_the_llm(self, text, ly_do):
        assert _match_regex(text, NOW) is None, ly_do


class TestParseViTime:
    @pytest.mark.asyncio
    async def test_regex_path_never_calls_the_model(self, monkeypatch):
        def explode(*a, **kw):
            raise AssertionError("không được gọi LLM khi regex đã khớp")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", explode
        )
        result = await parse_vi_time("mai 3h chiều", NOW)

        assert result.start_at == at(8, 15)
        assert result.source == "regex"

    @pytest.mark.asyncio
    async def test_llm_path_is_used_when_regex_defers(self, monkeypatch):
        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, messages, **kwargs):
                return ParsedTime(start_at=at(8, 9), source="llm")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model",
            lambda **kw: FakeModel(),
        )
        result = await parse_vi_time("sáng mai lúc 9 giờ", NOW)

        assert result.start_at == at(8, 9)
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_llm_result_still_goes_through_the_guard(self, monkeypatch):
        """Một lớp kiểm tra, hai chỗ sinh dữ liệu."""
        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, messages, **kwargs):
                return ParsedTime(
                    start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ), source="llm"
                )

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model",
            lambda **kw: FakeModel(),
        )
        assert (await parse_vi_time("thứ Sáu tuần sau", NOW)).start_at is None

    @pytest.mark.asyncio
    async def test_model_failure_asks_the_customer_instead_of_guessing(self, monkeypatch):
        class Broken:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                raise RuntimeError("Azure nghẽn")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", lambda **kw: Broken()
        )
        result = await parse_vi_time("thứ Năm tuần sau", NOW)

        assert result.start_at is None
        assert result.missing == ["giờ cụ thể"]

    @pytest.mark.asyncio
    async def test_a_slow_model_does_not_keep_the_customer_waiting(self, monkeypatch):
        import asyncio

        class Slow:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                await asyncio.sleep(5)
                return ParsedTime(start_at=at(8, 15), source="llm")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", lambda **kw: Slow()
        )
        result = await parse_vi_time("thứ Năm tuần sau", NOW, timeout=0.2)
        assert result.start_at is None

    @pytest.mark.asyncio
    async def test_parser_model_is_not_tagged_respond(self, monkeypatch):
        """Nếu model của parser mang tag respond, khách sẽ thấy
        `{"start_at": "2026-08-...` chạy ngang giữa cuộc trò chuyện."""
        seen = {}

        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                return ParsedTime(start_at=at(8, 9), source="llm")

        def spy(**kwargs):
            seen.update(kwargs)
            return FakeModel()

        monkeypatch.setattr("app.agents.booking_graph.timeparse.build_chat_model", spy)
        await parse_vi_time("thứ Năm tuần sau", NOW)

        assert "respond" not in seen["tags"]
        assert seen["streaming"] is False


def test_timeout_leaves_room_for_a_real_azure_call():
    """2 giây là quá chặt: đo thật thấy một lượt parse mất 2,2–2,4 giây.

    Ngưỡng thấp không làm test nào đỏ — `parse_vi_time` fail-soft, trả về
    "thiếu giờ cụ thể" y như khi câu thật sự thiếu giờ. Hậu quả chỉ lộ ra khi
    nói chuyện thật: khách đáp "9 giờ" và bị hỏi lại đúng câu vừa hỏi. Nên phải
    có một test khẳng định thẳng cái ngưỡng.
    """
    from app.agents.booking_graph.timeparse import PARSE_TIMEOUT_SECONDS

    assert PARSE_TIMEOUT_SECONDS >= 5.0
