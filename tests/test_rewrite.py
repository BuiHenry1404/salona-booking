import pytest
from langchain_core.messages import AIMessage

from app.agents.booking_graph.context import build_context_block
from app.agents.booking_graph.rewrite import REWRITE_TAG, _address_from_block, rewrite_once
from app.models.shop import ShopStatusView
from app.models.user import User


USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")


class ScriptedModel:
    def __init__(self, reply, fail=False):
        self.reply, self.fail, self.calls, self.kwargs = reply, fail, [], None

    async def ainvoke(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("azure down")
        return AIMessage(content=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply, fail=False):
        m = ScriptedModel(reply, fail)
        def _build(**kwargs):
            m.kwargs = kwargs
            return m
        monkeypatch.setattr("app.agents.booking_graph.rewrite.build_chat_model", _build)
        return m
    return _install


class TestRewriteOnce:
    """Phần LLM của guard: viết lại MỘT lần. Luật quyết khi nào gọi và dùng bản
    nào nằm ở `guard.apply_guard` — test ở `test_mw_guard.py`."""

    async def test_rewrites_with_the_violation_hints_and_previous_reply(self, patch_model):
        model = patch_model("Dạ anh Tám, phần giá để chủ tiệm báo anh nhé.")
        out = await rewrite_once("Chị hỏi chủ tiệm giúp em nhé.", ["pronoun", "repeat"],
                                 ["Câu trước."], "Gọi khách là: anh Tám.")
        assert out == "Dạ anh Tám, phần giá để chủ tiệm báo anh nhé."
        prompt = model.calls[0][0].content
        assert "pronoun" in prompt and "repeat" in prompt and "Câu trước." in prompt
        assert "Address the customer as: anh Tám" in prompt
        assert model.kwargs["tags"] == [REWRITE_TAG] and model.kwargs["streaming"] is False

    async def test_the_rewriter_sees_the_same_window_the_guard_compared_against(self, patch_model):
        """`repeats()` so với REPEAT_LOOKBACK câu đáp gần nhất, nhưng rewrite chỉ
        được cho xem ĐÚNG MỘT câu cuối. Nên guard bắt lặp với câu ở 3 lượt trước,
        rồi đưa cho người viết lại một câu KHÁC kèm lệnh "đừng lặp câu này" —
        nó không bao giờ nhìn thấy câu mình đã trùng."""
        model = patch_model("Dạ anh Tám, em xem giúp anh nhé.")
        cu = "Dạ anh Tám, em chỉ xem lịch của chính anh thôi ạ."
        await rewrite_once("Chị hỏi chủ tiệm giúp em nhé.", ["repeat"],
                           [cu, "Câu giữa.", "Câu áp chót.", "Câu trước."], "")
        prompt = model.calls[0][0].content
        assert cu in prompt, "câu bị trùng thật sự phải có trong prompt viết lại"

    async def test_model_failure_returns_none(self, patch_model):
        patch_model("", fail=True)
        assert await rewrite_once("Chị ơi.", ["pronoun"], [], "") is None

    async def test_empty_rewrite_returns_none(self, patch_model):
        patch_model("   ")
        assert await rewrite_once("Chị ơi.", ["pronoun"], [], "") is None

    async def test_slow_rewrite_returns_none(self, patch_model, monkeypatch):
        import asyncio

        from app.agents.booking_graph import rewrite

        class Slow:
            async def ainvoke(self, messages, **kw):
                await asyncio.sleep(5)

        monkeypatch.setattr(rewrite, "build_chat_model", lambda **kw: Slow())
        monkeypatch.setattr(rewrite, "REWRITE_TIMEOUT_SECONDS", 0.05)
        assert await rewrite_once("Chị ơi.", ["pronoun"], [], "") is None

    async def test_rewrite_tag_is_never_respond(self):
        assert REWRITE_TAG != "respond"


class TestAddressFromBlock:
    def test_reads_the_address_from_the_real_context_block(self):
        block = build_context_block(
            User(phone="0912345678", hashed_password="x", full_name="Chú Tám"),
            ShopStatusView(is_busy=False), [],
        )
        assert _address_from_block(block) == "anh Tám"
