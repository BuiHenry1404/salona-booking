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
        if callable(self.reply):
            return self.reply(messages)
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


QUESTION = "Dạ em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho chị Lan, chị chốt giúp em nhé?"


def simulate_turn_analysis(messages):
    human_content = messages[-1].content if messages else ""
    customer_text = ""
    for line in str(human_content).splitlines():
        if line.startswith("Customer's new message:"):
            customer_text = line.split("Customer's new message:", 1)[1].strip()
            break
    has_pending = "(no pending proposal)" not in str(human_content)

    # 6. Appointment lookups (Edge Case): 'chị xem lại lịch hẹn của chị với'
    if "xem lại lịch hẹn" in customer_text:
        return TurnAnalysis(route="booking", aside=None)

    # 5. Relative inquiries: 'thằng cháu có rảnh không em'
    if "thằng cháu" in customer_text or "rảnh không" in customer_text:
        return TurnAnalysis(route="booking", aside="relative_free")

    # 4. Deferrals: 'để chị xem lại đã', 'để cô hỏi thằng Huy đã'
    if "xem lại đã" in customer_text or "hỏi thằng huy" in customer_text.lower():
        return TurnAnalysis(route="booking", aside="defer_only")

    # Proposal replies (chỉ khi có pending proposal)
    if has_pending:
        # 3. Changes: 'đổi sang 4h chiều mai', 'thôi 5h đi em'
        if "đổi sang" in customer_text or "thôi 5h" in customer_text:
            time_text = "4h chiều mai" if "4h chiều mai" in customer_text else "5h"
            return TurnAnalysis(route="booking", reply_to_proposal="change", time_text=time_text)

        # 2. Rejections: 'thôi bận rồi', 'không làm nữa', 'hủy đi em'
        if any(w in customer_text for w in ["thôi bận rồi", "không làm nữa", "hủy đi em"]):
            return TurnAnalysis(route="booking", reply_to_proposal="reject")

        # 1. Approvals: 'ừ em', 'chốt cho chị', 'ok làm đi', 'đặt giờ đó nha'
        if any(w in customer_text for w in ["ừ em", "chốt cho chị", "ok làm đi", "đặt giờ đó nha"]):
            return TurnAnalysis(route="booking", reply_to_proposal="approve")

    return TurnAnalysis(route="booking")


# ==============================================================================
# Comprehensive Semantic Intent & Aside Tests (Task 4)
# ==============================================================================

class TestSemanticIntentClassification:
    """Kiểm thử ngữ nghĩa toàn diện thay thế hoàn toàn các từ điển tĩnh cũ:
    1. Đồng ý: 'ừ em', 'chốt cho chị', 'ok làm đi', 'đặt giờ đó nha' -> reply_to_proposal == 'approve'
    2. Từ chối: 'thôi bận rồi', 'không làm nữa', 'hủy đi em' -> reply_to_proposal == 'reject'
    3. Đổi giờ: 'đổi sang 4h chiều mai', 'thôi 5h đi em' -> reply_to_proposal == 'change'
    4. Hoãn: 'để chị xem lại đã', 'để cô hỏi thằng Huy đã' -> aside == 'defer_only'
    5. Hỏi người nhà: 'thằng cháu có rảnh không em' -> aside == 'relative_free'
    6. Tra cứu lịch (Edge Case): 'chị xem lại lịch hẹn của chị với' -> route == 'booking', aside is None
    """

    @pytest.mark.parametrize("text", [
        "ừ em",
        "chốt cho chị",
        "ok làm đi",
        "đặt giờ đó nha",
    ])
    async def test_approvals_with_structured_model(self, patch_model, text):
        box = patch_model(reply=TurnAnalysis(route="booking", reply_to_proposal="approve"))
        result = await analyze_turn([], text, PENDING, bot_question=QUESTION)
        assert result.reply_to_proposal == "approve"
        assert result.route == "booking"
        assert text in box["structured"].sent[-1].content

    @pytest.mark.parametrize("text", [
        "thôi bận rồi",
        "không làm nữa",
        "hủy đi em",
    ])
    async def test_rejections_with_structured_model(self, patch_model, text):
        box = patch_model(reply=TurnAnalysis(route="booking", reply_to_proposal="reject"))
        result = await analyze_turn([], text, PENDING, bot_question=QUESTION)
        assert result.reply_to_proposal == "reject"
        assert result.route == "booking"
        assert text in box["structured"].sent[-1].content

    @pytest.mark.parametrize("text,snippet", [
        ("đổi sang 4h chiều mai", "4h chiều mai"),
        ("thôi 5h đi em", "5h"),
    ])
    async def test_changes_with_structured_model(self, patch_model, text, snippet):
        box = patch_model(reply=TurnAnalysis(route="booking", reply_to_proposal="change", time_text=snippet))
        result = await analyze_turn([], text, PENDING, bot_question=QUESTION)
        assert result.reply_to_proposal == "change"
        assert result.route == "booking"
        assert result.time_text == snippet
        assert text in box["structured"].sent[-1].content

    @pytest.mark.parametrize("text", [
        "để chị xem lại đã",
        "để cô hỏi thằng Huy đã",
    ])
    async def test_deferrals_with_structured_model(self, patch_model, text):
        box = patch_model(reply=TurnAnalysis(route="booking", aside="defer_only"))
        result = await analyze_turn([], text, None)
        assert result.aside == "defer_only"
        assert text in box["structured"].sent[-1].content

    async def test_relative_inquiries_with_structured_model(self, patch_model):
        text = "thằng cháu có rảnh không em"
        box = patch_model(reply=TurnAnalysis(route="booking", aside="relative_free"))
        result = await analyze_turn([], text, None)
        assert result.aside == "relative_free"
        assert text in box["structured"].sent[-1].content

    async def test_appointment_lookup_edge_case_not_mistaken_as_deferral(self, patch_model):
        """Khách tra cứu lịch 'chị xem lại lịch hẹn của chị với':
        Không bị nhầm thành hoãn (aside is None / 'none'), route='booking'."""
        text = "chị xem lại lịch hẹn của chị với"
        box = patch_model(reply=TurnAnalysis(route="booking", aside=None))
        result = await analyze_turn([], text, None)
        assert result.route == "booking"
        assert result.aside is None
        assert text in box["structured"].sent[-1].content

    @pytest.mark.parametrize("text,expected_reply,expected_aside,expected_route,pending", [
        ("ừ em", "approve", None, "booking", PENDING),
        ("chốt cho chị", "approve", None, "booking", PENDING),
        ("ok làm đi", "approve", None, "booking", PENDING),
        ("đặt giờ đó nha", "approve", None, "booking", PENDING),
        ("thôi bận rồi", "reject", None, "booking", PENDING),
        ("không làm nữa", "reject", None, "booking", PENDING),
        ("hủy đi em", "reject", None, "booking", PENDING),
        ("đổi sang 4h chiều mai", "change", None, "booking", PENDING),
        ("thôi 5h đi em", "change", None, "booking", PENDING),
        ("để chị xem lại đã", None, "defer_only", "booking", None),
        ("để cô hỏi thằng Huy đã", None, "defer_only", "booking", None),
        ("thằng cháu có rảnh không em", None, "relative_free", "booking", None),
        ("chị xem lại lịch hẹn của chị với", None, None, "booking", None),
    ])
    async def test_simulated_semantic_parsing_end_to_end(
        self, patch_model, text, expected_reply, expected_aside, expected_route, pending
    ):
        patch_model(reply=simulate_turn_analysis)
        result = await analyze_turn([], text, pending, bot_question=QUESTION)
        assert result.route == expected_route
        assert result.reply_to_proposal == expected_reply
        assert result.aside == expected_aside


@pytest.mark.llm
class TestSemanticClassificationRealLLM:
    """Gọi Azure OpenAI THẬT: pytest tests/test_analyzer.py -m llm -v.
    Đo trực tiếp prompt và model parsing trên tất cả các trường hợp ngữ nghĩa."""

    @pytest.mark.parametrize("text,expected", [
        ("ừ em", "approve"),
        ("chốt cho chị", "approve"),
        ("ok làm đi", "approve"),
        ("đặt giờ đó nha", "approve"),
        ("thôi bận rồi", "reject"),
        ("không làm nữa", "reject"),
        ("hủy đi em", "reject"),
        ("đổi sang 4h chiều mai", "change"),
        ("thôi 5h đi em", "change"),
    ])
    async def test_proposal_semantic_replies_llm(self, text, expected):
        result = await analyze_turn([], text, PENDING, bot_question=QUESTION)
        assert result.reply_to_proposal == expected

    @pytest.mark.parametrize("text,expected_aside", [
        ("để chị xem lại đã", "defer_only"),
        ("để cô hỏi thằng Huy đã", "defer_only"),
        ("thằng cháu có rảnh không em", "relative_free"),
    ])
    async def test_asides_semantic_classification_llm(self, text, expected_aside):
        result = await analyze_turn([], text, None)
        assert result.aside == expected_aside

    async def test_lookup_edge_case_not_deferral_llm(self):
        result = await analyze_turn([], "chị xem lại lịch hẹn của chị với", None)
        assert result.route == "booking"
        assert result.aside in (None, "none")
        assert result.aside != "defer_only"


