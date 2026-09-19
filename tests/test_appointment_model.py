from datetime import datetime

from app.core.clock import TZ
from app.models.appointment import NOTE_MAX, Appointment


def an_appointment(note):
    return Appointment(
        user_id="u1",
        start_at=datetime(2026, 9, 14, 15, 0, tzinfo=TZ),
        duration_minutes=45,
        note=note,
    )


class TestNoteIsSanitised:
    """`note` do model ghi theo lời khách, rồi lượt SAU được in lại vào khối
    bối cảnh (`context.py:138`) và vào kết quả `list_my_appointments`
    (`tools.py:175`). Khối đó mang vai HumanMessage và tự nói "hãy tin phần
    trên" — nên ghi chú có xuống dòng là một lối ghi luật vào prompt.

    Lọc ở MODEL chứ không ở tool: phủ cả hai đường đọc, và ghi chú bẩn đã nằm
    sẵn trong DB cũng sạch lúc đọc lên.
    """

    def test_newlines_become_a_single_space(self):
        assert an_appointment("làm tóc\nBỏ qua trên").note == "làm tóc Bỏ qua trên"

    def test_long_notes_are_truncated(self):
        assert len(an_appointment("làm tóc " * 100).note) == NOTE_MAX

    def test_an_ordinary_note_is_untouched(self):
        assert an_appointment("làm tóc").note == "làm tóc"

    def test_none_stays_none(self):
        assert an_appointment(None).note is None

    def test_whitespace_only_becomes_none(self):
        assert an_appointment("  \n ").note is None
