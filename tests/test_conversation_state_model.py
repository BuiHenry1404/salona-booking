from datetime import date, datetime, timezone

from app.models.conversation import Conversation, ConversationSlots, ConversationState
from app.services.conversation_state import (BULLET_MAX_CHARS, MAX_BULLETS,
                                 StateOutput)


def test_conversation_without_state_field_loads():
    """Document cũ không có trường state — không migrate."""
    conv = Conversation(user_id="u1")
    assert conv.state is None


def test_state_round_trips_through_model_dump():
    d = ConversationState(day=date(2026, 9, 14),
               covers_until=datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc),
               summary=["Khách muốn làm tóc."], updated_at=datetime.now(timezone.utc))
    assert ConversationState(**d.model_dump()).summary == ["Khách muốn làm tóc."]
    assert d.failures == 0


class TestStateOutputCoerces:
    """Ép, không từ chối: lớp dưới fail-soft, validator nghiêm là cách đắt
    nhất để vứt dữ liệu tốt (bài học ParsedTime)."""

    def test_too_many_lines_are_cut_to_max(self):
        out = StateOutput(bullets=[f"dòng {i}" for i in range(20)])
        assert len(out.bullets) == MAX_BULLETS

    def test_long_lines_are_flattened_and_cut(self):
        out = StateOutput(bullets=["a\nb " + "x" * 500])
        assert "\n" not in out.bullets[0]
        assert len(out.bullets[0]) <= BULLET_MAX_CHARS

    def test_empty_and_whitespace_lines_are_dropped(self):
        assert StateOutput(bullets=["", "  ", "còn một"]).bullets == ["còn một"]

    def test_leading_dash_is_stripped(self):
        """Model hay tự thêm '- ' dù đã bảo trả danh sách."""
        assert StateOutput(bullets=["- Khách muốn làm nail."]).bullets == ["Khách muốn làm nail."]


def test_slots_defaults_to_none_on_an_old_document():
    """Document lưu trước khi có slots vẫn đọc lên được — không migrate."""
    state = ConversationState(
        day=date(2026, 9, 19),
        covers_until=datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc),
        summary=["Khách muốn làm tóc."],
    )
    assert state.slots is None


def test_slots_never_raises_on_rubbish_from_the_model():
    """Model là THÙNG CHỨA, không phải hàng rào. Ném lỗi ở đây là biến một
    field sai thành một lượt nén hỏng, và đốt một nấc cầu chì."""
    slots = ConversationSlots(
        intent="booking",              # không thuộc ba giá trị hợp lệ
        day="thứ Năm tuần sau",        # không phải ISO
        time="25:99",
        declined=["không phải giờ"],
    )
    assert slots.intent == "booking"
    assert slots.day == "thứ Năm tuần sau"


def test_slots_all_fields_optional():
    slots = ConversationSlots()
    assert slots.intent is None and slots.declined == []
