"""Tests cho `app/agents/booking_graph/relatives.py`.

Nhận diện và xử lý qua `TurnAnalysis.aside`:
- `relative_free`: Khách hỏi người quen có rảnh không.
- `defer_only`: Khách hoãn, không có yêu cầu khác.
- `defer_with_request`: Khách hoãn nhưng có yêu cầu kèm theo.
"""
import inspect
import pytest

from app.agents.booking_graph import relatives
from app.agents.booking_graph.relatives import (
    DEFERRAL_LINE,
    RELATIVE_LINE,
    aside_line,
    deferral_reply,
)


def test_aside_line_purely_reads_analysis():
    assert "[relative]" in aside_line("câu bất kỳ", analysis={"aside": "relative_free"})
    assert "[deferral]" in aside_line("câu bất kỳ", analysis={"aside": "defer_only"})
    assert "[deferral]" in aside_line("câu bất kỳ", analysis={"aside": "defer_with_request"})
    assert aside_line("câu bất kỳ", analysis={"aside": "none"}) == ""
    assert aside_line("câu bất kỳ", analysis={}) == ""
    assert aside_line("câu bất kỳ", analysis=None) == ""


def test_deferral_reply_purely_reads_analysis():
    reply = deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "defer_only"})
    assert reply and "chị Lan" in reply and "nhắn" in reply
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "defer_with_request"}) is None
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "relative_free"}) is None
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "none"}) is None
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={}) is None
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis=None) is None


def test_aside_line_and_deferral_reply_with_object_analysis():
    class DummyAnalysis:
        def __init__(self, aside):
            self.aside = aside

    assert "[relative]" in aside_line("câu bất kỳ", analysis=DummyAnalysis("relative_free"))
    assert "[deferral]" in aside_line("câu bất kỳ", analysis=DummyAnalysis("defer_only"))
    reply = deferral_reply("câu bất kỳ", "chị Lan", [], analysis=DummyAnalysis("defer_only"))
    assert reply and "chị Lan" in reply and "nhắn" in reply
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis=DummyAnalysis("none")) is None


def test_aside_line_does_not_inspect_text_content():
    # Câu trước đây từng kích hoạt qua string matching nay không còn nếu không có analysis
    assert aside_line("khoan da, de chu hoi lai no co ranh khong") == ""
    assert deferral_reply("khoan da, de chu hoi lai no co ranh khong", "chú Nam", []) is None


def test_two_deferrals_in_a_row_get_different_words():
    first = deferral_reply("khoan đã", "anh Hùng", [], analysis={"aside": "defer_only"})
    second = deferral_reply("để hỏi lại", "anh Hùng", [first], analysis={"aside": "defer_only"})
    third = deferral_reply("để tính sau", "anh Hùng", [first, second], analysis={"aside": "defer_only"})
    assert len({first, second, third}) == 3


def test_deferral_reply_fallback_when_all_options_seen():
    first = deferral_reply("khoan đã", "anh Hùng", [], analysis={"aside": "defer_only"})
    second = deferral_reply("để hỏi lại", "anh Hùng", [first], analysis={"aside": "defer_only"})
    third = deferral_reply("để tính sau", "anh Hùng", [first, second], analysis={"aside": "defer_only"})
    fourth = deferral_reply("thong thả nha", "anh Hùng", [first, second, third], analysis={"aside": "defer_only"})
    assert fourth is not None
    assert "anh Hùng" in fourth
