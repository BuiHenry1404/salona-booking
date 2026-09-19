import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph.guard import make_guard_node
from app.agents.booking_graph.phrase import make_phrase_node
from app.models.user import User

pytestmark = pytest.mark.asyncio
USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")
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
    return {"messages": [HumanMessage(content="anh bận đột xuất, dời sang mai nha"), HumanMessage(content="ừ")],
            "context_block": "Gọi khách là: anh Tám.", "confirm_fact": FACT, "fallback": FALLBACK,
            "previous_replies": [], "customer_text": "ừ", **extra}


class TestPhraseNode:
    async def test_writes_a_draft_with_the_fact_and_streams_as_respond(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi, mai Thứ Ba 15/9, 9 giờ sáng gặp anh nhé.")
        out = await make_phrase_node()(a_state())
        assert out["draft"].startswith("Dạ anh Tám") and out["phrase_fact"] == FACT
        assert model.kwargs["tags"] == ["respond"]
        system = model.calls[0][0].content
        assert "Thứ Ba 15/9, 9 giờ sáng" in system and "anh Tám" in system
        # lịch sử đi vào để bắt mạch
        assert any("bận đột xuất" in m.content for m in model.calls[0])

    async def test_model_failure_yields_the_fallback_as_draft(self, patch_model):
        patch_model("", fail=True)
        out = await make_phrase_node()(a_state())
        assert out["draft"] == FALLBACK and out["phrase_fact"] == FACT

    async def test_state_is_sent_right_after_the_system_message(self, patch_model):
        """F8: `phrase` phải mang state như `agents.py` — không thì lịch sử bị
        tóm tắt (state thay chỗ) mất tích trong đúng lượt chốt lịch."""
        model = patch_model("Dạ anh Tám, em dời xong rồi nhé.")
        out = await make_phrase_node()(a_state(summary=["Khách muốn cắt tóc."]))
        second = model.calls[0][1]
        assert "Khách muốn cắt tóc." in second.content

    async def test_no_state_leaves_the_message_order_unchanged(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi nhé.")
        out = await make_phrase_node()(a_state())
        assert len(model.calls[0]) == 4  # system, context_block, history(1), question(1)

    async def test_failed_kind_drops_the_must_include_when_line_entirely(self, patch_model):
        """Task 4 review: fact["when"] là None khi đặt lịch thất bại (kind
        "failed"). Prompt cũ nhét placeholder tiếng Anh "(no time — the
        booking failed)" vào đúng chỗ câu "MUST include this exact text" —
        placeholder đó có thể rò vào câu trả lời tiếng Việt. Khi không có
        `when`, dòng MUST-include không được xuất hiện chút nào."""
        model = patch_model("Dạ Tám ơi, giờ đó đã có người đặt, anh chọn giờ khác giúp em nhé.")
        fact = {**FACT, "kind": "failed", "when": None, "error": "Giờ đó đã có người đặt"}
        out = await make_phrase_node()(a_state(confirm_fact=fact))
        system = model.calls[0][0].content
        assert "MUST include this exact text" not in system
        assert "no time" not in system


class TestGuardForPhrase:
    async def test_missing_when_falls_back_without_rewrite(self):
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, em đặt xong rồi nhé."))
        assert out["answer"] == FALLBACK and out["answer_source"] == "code"

    async def test_wrong_address_falls_back(self):
        out = await make_guard_node(USER)(a_state(draft="Chị Tám ơi, Thứ Ba 15/9, 9 giờ sáng em đặt rồi ạ."))
        assert out["answer"] == FALLBACK

    async def test_good_phrase_is_used(self):
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em giữ cho anh rồi, mai gặp nhé."))
        assert out["answer"].startswith("Dạ anh Tám") and out["answer_source"] == "llm"

    async def test_failed_kind_must_carry_the_error(self):
        fact = {**FACT, "kind": "failed", "when": None, "error": "Giờ đó đã có người đặt"}
        fb = "Dạ Giờ đó đã có người đặt ạ. Anh Tám chọn giờ khác giúp em nhé."
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, em chưa đặt được ạ.", confirm_fact=fact, fallback=fb))
        assert out["answer"] == fb
