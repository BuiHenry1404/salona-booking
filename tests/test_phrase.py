import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph.phrase import code_phrase, write_phrase

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
        # `summary` giờ là chuỗi đã dựng sẵn (Task 12c), không còn List[str].
        model = patch_model("Dạ anh Tám, em dời xong rồi nhé.")
        await write_phrase(a_state(summary="Khách muốn cắt tóc."))
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

    async def test_queued_requests_are_stated_as_not_held(self, patch_model):
        """Lỗi live B3 #1: câu chốt bịa "2 giờ em cũng đã giữ cho cháu rồi" khi
        2 giờ chỉ đang CHỜ. Đầu vào phrase phải nói rõ: chưa giữ, chưa đặt."""
        model = patch_model("Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em ghi xong rồi ạ.")
        fact = {**FACT, "queued_when": ["Thứ Ba 15/9, 2 giờ chiều"]}
        await write_phrase(a_state(confirm_fact=fact))
        system = model.calls[0][0].content
        assert "Thứ Ba 15/9, 2 giờ chiều" in system
        assert "NOT held" in system and "NOT booked" in system

    async def test_no_queue_no_queue_sentence(self, patch_model):
        model = patch_model("Dạ anh Tám, em ghi xong rồi ạ.")
        await write_phrase(a_state())
        assert "NOT held" not in model.calls[0][0].content

    async def test_phrase_prompt_mentions_context_and_last_reply(self, patch_model):
        """Task 15: prompt phải đọc `fact["context"]` (gợi ý giọng theo mạch)
        và câu đáp LLM gần nhất (`previous_replies[-1]`) — để không lặp khung
        câu cũ. Cả hai nằm trong SYSTEM, không thêm message mới (xem test
        `test_no_state_leaves_the_message_order_unchanged` — số message không đổi)."""
        model = patch_model("Dạ anh Tám, lần này em đặt vào Thứ Ba 15/9, 9 giờ sáng rồi ạ.")
        fact = {**FACT, "context": "returning"}
        previous = ["Dạ anh Tám, em ghi xong lịch cho anh rồi ạ."]
        await write_phrase(a_state(confirm_fact=fact, previous_replies=previous))
        system = model.calls[0][0].content
        assert "returning customer" in system
        # Task 15 review: bọc «» giống hệt dòng "Câu em vừa trả lời ở lượt
        # trước" trong khối bối cảnh (`context.py`), không chèn trần.
        assert f"«{previous[0]}»" in system

    async def test_phrase_prompt_last_reply_with_newlines_becomes_one_line_in_guillemets(self, patch_model):
        """Task 15 review: câu đáp cũ có xuống dòng (vd. do rewrite cũ) không
        được chèn nguyên văn vào SYSTEM — `single_line` ép về một dòng trước,
        giống dòng "Câu em vừa trả lời ở lượt trước" của khối bối cảnh."""
        model = patch_model("Dạ anh Tám, em dời xong rồi ạ.")
        previous = ["Dạ anh Tám,\nem ghi xong   lịch\ncho anh rồi ạ."]
        await write_phrase(a_state(previous_replies=previous))
        system = model.calls[0][0].content
        assert "\n" not in system.split("«", 1)[1].split("»", 1)[0]
        assert "«Dạ anh Tám, em ghi xong lịch cho anh rồi ạ.»" in system

    async def test_phrase_prompt_without_a_previous_reply_has_no_last_reply_rule(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi ạ.")
        await write_phrase(a_state())  # previous_replies=[] mặc định ở a_state()
        system = model.calls[0][0].content
        assert "last reply" not in system

    async def test_phrase_prompt_defaults_missing_context_to_first_time(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi ạ.")
        fact = {k: v for k, v in FACT.items() if k != "context"}
        await write_phrase(a_state(confirm_fact=fact))
        system = model.calls[0][0].content
        assert "returning customer" not in system
        assert "changed their mind" not in system


class TestCodePhrase:
    """`code_phrase`: câu chốt đường nút, viết bằng CODE — không gọi LLM."""

    def test_code_phrase_avoids_a_template_already_used(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan",
                "context": "first_time"}
        first = code_phrase(fact, [])
        second = code_phrase(fact, [first])
        assert first != second and "3 giờ chiều" in second and "chị Lan" in second

    def test_code_phrase_failed_uses_fallback(self):
        assert code_phrase({"kind": "failed", "fallback": "X"}, []) == "X"

    def test_code_phrase_defaults_missing_context_to_first_time(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan"}
        with_context = code_phrase({**fact, "context": "first_time"}, [])
        without_context = code_phrase(fact, [])
        assert without_context == with_context

    def test_code_phrase_runs_out_of_templates_falls_back_to_first(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan",
                "context": "first_time"}
        first = code_phrase(fact, [])
        second = code_phrase(fact, [first])
        third = code_phrase(fact, [first, second])
        # Cả 3 mẫu đều đã "dùng" (coi như trùng) — hết mẫu rơi về mẫu đầu.
        exhausted = code_phrase(fact, [first, second, third])
        assert exhausted == first
