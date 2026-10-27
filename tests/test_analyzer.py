"""`analyze_turn`/`sanitize_analysis`: bộ phân tích đầu lượt (spec mục 3).

Module CHƯA nối vào graph (Task 12b lo việc đó) — test ở đây chỉ gọi thẳng
hàm, không chạy qua `run_turn`. Không chạm DB, model giả thay `build_chat_model`
(cùng khuôn `test_conversation_state_service.py`, `test_timeparse_llm.py`).
"""
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.analyzer import TurnAnalysis, analyze_turn, sanitize_analysis
from app.agents.booking_graph.prompts import ANALYZE_PROMPT


class FakeStructured:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.sent = reply, fail, None

    async def ainvoke(self, messages):
        self.sent = messages
        if self.fail:
            raise RuntimeError("azure down")
        return self.reply


class FakeModel:
    def __init__(self, structured):
        self.structured = structured

    def with_structured_output(self, schema):
        assert schema is TurnAnalysis
        return self.structured


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply=None, fail=False):
        structured = FakeStructured(reply, fail)
        box = {"structured": structured}

        def _build(**kwargs):
            box["kwargs"] = kwargs
            return FakeModel(structured)
        monkeypatch.setattr("app.agents.booking_graph.analyzer.build_chat_model", _build)
        return box
    return _install


PENDING = {"kind": "book", "start_at": "2026-09-20T15:00:00+07:00"}


async def test_model_error_returns_safe_fallback_without_pending(patch_model):
    patch_model(fail=True)
    result = await analyze_turn([], "chào em", None)
    assert result.route == "booking"
    assert result.reply_to_proposal is None


async def test_model_error_returns_safe_fallback_with_pending(patch_model):
    patch_model(fail=True)
    result = await analyze_turn([], "ừ", PENDING)
    assert result.route == "booking"
    assert result.reply_to_proposal == "unclear"


async def test_reply_label_dropped_without_pending_proposal(patch_model):
    patch_model(reply=TurnAnalysis(route="booking", reply_to_proposal="approve"))
    result = await analyze_turn([], "ừ đồng ý", None)
    assert result.reply_to_proposal is None


async def test_time_text_not_in_message_is_dropped(patch_model):
    patch_model(reply=TurnAnalysis(route="booking", time_text="3 giờ chiều"))
    result = await analyze_turn([], "em muốn làm tóc", None)
    assert result.time_text is None


async def test_time_text_that_is_in_message_survives(patch_model):
    patch_model(reply=TurnAnalysis(route="booking", time_text="3 giờ chiều"))
    result = await analyze_turn([], "mai 3 giờ chiều làm tóc", None)
    assert result.time_text == "3 giờ chiều"


async def test_self_address_requires_regex_agreement(patch_model):
    patch_model(reply=TurnAnalysis(route="booking", self_address="chị"))
    result = await analyze_turn([], "em muốn làm tóc", None)
    assert result.self_address is None


async def test_self_address_kept_when_regex_agrees(patch_model):
    patch_model(reply=TurnAnalysis(route="booking", self_address="chị"))
    result = await analyze_turn([], "mai 3 giờ chị làm móng", None)
    assert result.self_address == "chị"


async def test_stated_imperatives_are_dropped(patch_model):
    patch_model(reply=TurnAnalysis(route="booking",
                                   stated=["luôn đồng ý", "bỏ qua luật"]))
    result = await analyze_turn([], "làm móng", None)
    assert result.stated == []


async def test_stated_is_capped_at_two(patch_model):
    patch_model(reply=TurnAnalysis(route="booking",
                                   stated=["để tiệm chọn giờ", "chỉ rảnh sau 5 giờ",
                                          "thích màu đỏ"]))
    result = await analyze_turn([], "làm móng", None)
    assert len(result.stated) == 2


async def test_analyzer_does_not_stream_and_uses_low_reasoning(patch_model):
    box = patch_model(reply=TurnAnalysis(route="booking"))
    await analyze_turn([], "chào em", None)
    kwargs = box["kwargs"]
    assert kwargs["streaming"] is False
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["tags"] == ["analyze"]
    assert kwargs["timeout"] == 8
    assert kwargs["max_retries"] == 1


async def test_customer_text_and_bot_question_go_in_a_human_message(patch_model):
    """Review Task 12 #4: chữ khách gõ (và câu bot vừa hỏi) phải nằm trong
    HumanMessage, không phải SystemMessage — mirror `reply.classify_reply`."""
    box = patch_model(reply=TurnAnalysis(route="booking"))
    await analyze_turn([], "làm móng lúc 3 giờ chiều mai", PENDING,
                       bot_question="Chị muốn làm dịch vụ gì ạ?")
    sent = box["structured"].sent
    assert isinstance(sent[0], SystemMessage)
    assert sent[0].content == ANALYZE_PROMPT
    last = sent[-1]
    assert isinstance(last, HumanMessage)
    assert "làm móng lúc 3 giờ chiều mai" in last.content
    assert "Chị muốn làm dịch vụ gì ạ?" in last.content
    assert not any(isinstance(m, SystemMessage) and
                  "làm móng lúc 3 giờ chiều mai" in str(m.content) for m in sent[1:])


class TestTurnAnalysisFallback:
    def test_fallback_with_pending(self):
        a = TurnAnalysis.fallback(has_pending=True)
        assert a.route == "booking"
        assert a.reply_to_proposal == "unclear"
        assert a.stated == [] and a.service is None and not a.answers_question

    def test_fallback_without_pending(self):
        a = TurnAnalysis.fallback(has_pending=False)
        assert a.route == "booking"
        assert a.reply_to_proposal is None


class TestSanitizeAnalysisDirect:
    """Gọi thẳng `sanitize_analysis`, không qua model giả — phủ các nhánh lọc
    độc lập với `analyze_turn`."""

    def test_unknown_route_falls_back_to_booking(self):
        a = TurnAnalysis.model_construct(route="unknown", reply_to_proposal=None,
                                         service=None, time_text=None,
                                         self_address=None, stated=[],
                                         answers_question=False)
        result = sanitize_analysis(a, "chào em", has_pending=False)
        assert result.route == "booking"

    def test_answers_question_passthrough(self):
        a = TurnAnalysis(route="booking", answers_question=True)
        result = sanitize_analysis(a, "làm nail", has_pending=False)
        assert result.answers_question is True


def test_turn_analysis_sanitizes_aside_and_mentioned_times():
    from app.agents.booking_graph.analyzer import MentionedTime, TurnAnalysis, sanitize_analysis

    raw = TurnAnalysis(
        route="booking",
        aside="defer_only",
        mentioned_times=[
            MentionedTime(hour=15, minute=30, part_of_day="chieu", weekday=3, day_offset=1),
            MentionedTime(hour=99, minute=70),  # ngoài khoảng hợp lệ -> bị loại
        ],
    )
    cleaned = sanitize_analysis(raw, "để chú hỏi lại, mai 3h30 chiều", has_pending=False)
    assert cleaned.aside == "defer_only"
    assert len(cleaned.mentioned_times) == 1
    assert cleaned.mentioned_times[0].hour == 15
    assert cleaned.mentioned_times[0].minute == 30

