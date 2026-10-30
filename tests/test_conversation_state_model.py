from datetime import date, datetime, timezone

from app.models.conversation import (Conversation, ConversationSlots, ConversationState,
                                     GoalProgress, QueuedRequest, StateDigest)
from app.services.conversation_state import StateOutput


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


def test_state_digest_defaults_to_empty_lists():
    digest = StateDigest()
    assert digest.wants == [] and digest.settled == [] and digest.declined == []
    assert digest.constraints == [] and digest.open == []


def test_conversation_state_digest_and_quotes_default_to_empty():
    """Tài liệu MỚI (sau Task 12c) mặc định chưa có digest — chỉ `_compact`
    điền vào. `customer_quotes` mặc định rỗng."""
    state = ConversationState(day=date(2026, 10, 26),
                              covers_until=datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc))
    assert state.digest is None
    assert state.customer_quotes == []


def test_state_round_trips_with_digest_and_quotes():
    digest = StateDigest(wants=["làm tóc"], constraints=["chỉ rảnh cuối tuần"])
    d = ConversationState(day=date(2026, 9, 14),
               covers_until=datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc),
               digest=digest, customer_quotes=["em muốn làm tóc"],
               updated_at=datetime.now(timezone.utc))
    restored = ConversationState(**d.model_dump())
    assert restored.digest.wants == ["làm tóc"]
    assert restored.customer_quotes == ["em muốn làm tóc"]


class TestStateOutputCarriesADigest:
    """`StateOutput` giờ chỉ là THÙNG CHỨA THÔ (như `ConversationSlots`) —
    lọc/cắt nằm ở `sanitize_digest` (conversation_state.py), không ở đây."""

    def test_digest_defaults_to_empty_when_not_given(self):
        out = StateOutput()
        assert out.digest == StateDigest()

    def test_digest_round_trips_as_given(self):
        out = StateOutput(digest=StateDigest(wants=["làm nail"]))
        assert out.digest.wants == ["làm nail"]


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


def test_goal_and_queued_default_to_none_and_empty_on_an_old_document():
    """Document cũ (trước task này) không có `goal`/`queued` — vẫn đọc được,
    không migrate: `goal` -> None, `queued` -> []."""
    state = ConversationState(
        day=date(2026, 9, 19),
        covers_until=datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc),
    )
    assert state.goal is None
    assert state.queued == []


def test_goal_and_queued_round_trip_through_model_dump():
    goal = GoalProgress(goal_id="g1", turns_without_progress=2,
                        last_marker="hour_proposed", service="làm móng gel",
                        appointment_id="a1")
    queued = QueuedRequest(queue_id="q1", start_at=datetime(2026, 10, 31, 9, 0,
                                                            tzinfo=timezone.utc),
                           spoken="9 giờ sáng mai", queued_at=datetime.now(timezone.utc))
    state = ConversationState(day=date(2026, 10, 30),
                              covers_until=datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc),
                              goal=goal, queued=[queued])
    restored = ConversationState(**state.model_dump())
    assert restored.goal == goal
    assert restored.queued[0].queue_id == "q1"
    assert restored.queued[0].start_at.tzinfo is not None
