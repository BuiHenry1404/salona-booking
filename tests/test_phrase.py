import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph.phrase import write_phrase

FACT = {"kind": "booked", "when": "Thứ Ba 15/9, 9 giờ sáng", "note": "cắt tóc", "address": "anh Tám", "error": None}
FALLBACK = "Xong rồi ạ. Hẹn gặp anh Tám Thứ Ba 15/9, 9 giờ sáng nhé."


class ScriptedModel:
    def __init__(self, reply, fail=False):
        self.reply, self.fail, self.calls, self.kwargs = reply, fail, [], None
    async def ainvoke(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("down")
        return AIMessage(content=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply, fail=False):
        m = ScriptedModel(reply, fail)
        def _build(**kwargs):
            m.kwargs = kwargs
            return m
        monkeypatch.setattr("app.agents.booking_graph.phrase.build_chat_model", _build)
        return m
    return _install


def a_state(**extra):
    # Lịch sử là một cặp hỏi/đáp như ngoài đời: câu khách không có câu đáp
    # ngay sau là dấu vết lượt lỗi, `model_history` bỏ nó (giống A).
    return {"messages": [HumanMessage(content="anh bận đột xuất, dời sang mai nha"),
                         AIMessage(content="Dạ mai 9 giờ sáng được không anh?"),
                         HumanMessage(content="ừ")],
            "context_block": "Gọi khách là: anh Tám.", "confirm_fact": FACT, "fallback": FALLBACK,
            "previous_replies": [], "customer_text": "ừ", **extra}


class TestWritePhrase:
    """Câu chốt LLM viết; guard kiểm nó ở `test_mw_guard.py` (TestGuardForPhrase),
    middleware gọi nó ở `test_mw_phrase.py`."""

    async def test_writes_a_draft_with_the_fact_and_streams_as_respond(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi, mai Thứ Ba 15/9, 9 giờ sáng gặp anh nhé.")
        out = await write_phrase(a_state())
        assert out.startswith("Dạ anh Tám")
        assert model.kwargs["tags"] == ["respond"]
        system = model.calls[0][0].content
        assert "Thứ Ba 15/9, 9 giờ sáng" in system and "anh Tám" in system
        # lịch sử đi vào để bắt mạch
        assert any("bận đột xuất" in m.content for m in model.calls[0])

    async def test_model_failure_yields_the_fallback_as_draft(self, patch_model):
        patch_model("", fail=True)
        assert await write_phrase(a_state()) == FALLBACK

    async def test_state_is_sent_right_after_the_system_message(self, patch_model):
        """F8: `phrase` phải mang state như `agents.py` — không thì lịch sử bị
        tóm tắt (state thay chỗ) mất tích trong đúng lượt chốt lịch."""
        model = patch_model("Dạ anh Tám, em dời xong rồi nhé.")
        await write_phrase(a_state(summary=["Khách muốn cắt tóc."]))
        second = model.calls[0][1]
        assert "Khách muốn cắt tóc." in second.content

    async def test_no_state_leaves_the_message_order_unchanged(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi nhé.")
        await write_phrase(a_state())
        assert len(model.calls[0]) == 5  # system, history(2), context_block, question(1)

    async def test_failed_kind_drops_the_must_include_when_line_entirely(self, patch_model):
        """Task 4 review: fact["when"] là None khi đặt lịch thất bại (kind
        "failed"). Prompt cũ nhét placeholder tiếng Anh "(no time — the
        booking failed)" vào đúng chỗ câu "MUST include this exact text" —
        placeholder đó có thể rò vào câu trả lời tiếng Việt. Khi không có
        `when`, dòng MUST-include không được xuất hiện chút nào."""
        model = patch_model("Dạ Tám ơi, giờ đó đã có người đặt, anh chọn giờ khác giúp em nhé.")
        fact = {**FACT, "kind": "failed", "when": None, "error": "Giờ đó đã có người đặt"}
        await write_phrase(a_state(confirm_fact=fact))
        system = model.calls[0][0].content
        assert "MUST include this exact text" not in system
        assert "no time" not in system
