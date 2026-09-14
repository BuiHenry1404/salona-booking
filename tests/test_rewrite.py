import pytest
from langchain_core.messages import AIMessage

from app.agents.booking_graph.guard import make_guard_node, route_after_guard
from app.agents.booking_graph.rewrite import REWRITE_TAG, make_rewrite_node
from app.models.user import User

pytestmark = pytest.mark.asyncio

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


def a_state(draft, previous=(), customer="giá bao nhiêu", **extra):
    return {"draft": draft, "previous_replies": list(previous), "customer_text": customer,
            "context_block": "", "messages": [], **extra}


class TestGuardNode:
    async def test_clean_draft_becomes_the_answer_unchanged(self):
        out = await make_guard_node(USER)(a_state("Dạ anh Tám, mai 9 giờ sáng ạ."))
        assert out["answer"] == "Dạ anh Tám, mai 9 giờ sáng ạ."
        assert out["answer_source"] == "llm"
        assert out.get("violations") == []

    async def test_violation_on_first_pass_requests_a_rewrite(self):
        out = await make_guard_node(USER)(a_state("Chị hỏi chủ tiệm giúp em nhé."))
        assert out["violations"] == ["pronoun"]
        assert "answer" not in out
        assert route_after_guard({**a_state("x"), **out}) == "rewrite"

    async def test_second_pass_with_violations_falls_back_to_the_original_draft(self):
        state = a_state("Chị vẫn sai nè.", rewritten=True, original_draft="Chị sai lần đầu.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Chị sai lần đầu."
        assert route_after_guard({**state, **out}) == "end"

    async def test_second_pass_clean_uses_the_rewrite(self):
        state = a_state("Dạ anh Tám, chủ tiệm sẽ báo giá ạ.", rewritten=True, original_draft="Chị hỏi chủ tiệm nhé.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Dạ anh Tám, chủ tiệm sẽ báo giá ạ."

    async def test_second_pass_that_lost_a_time_falls_back(self):
        state = a_state("Dạ anh Tám, em giữ chỗ rồi ạ.", rewritten=True,
                        original_draft="Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ."
        assert "content" in out["violations"]

    async def test_repeat_uses_previous_llm_replies(self):
        prev = ["Em chỉ xem và đặt lịch của anh Tám thôi. Anh muốn đặt hay xem lịch của mình ạ?"]
        out = await make_guard_node(USER)(a_state(prev[0], previous=prev))
        assert out["violations"] == ["repeat"]


class TestRewriteNode:
    async def test_rewrites_with_the_violation_hints_and_previous_reply(self, patch_model):
        model = patch_model("Dạ anh Tám, phần giá để chủ tiệm báo anh nhé.")
        state = a_state("Chị hỏi chủ tiệm giúp em nhé.", previous=["Câu trước."], violations=["pronoun", "repeat"])
        out = await make_rewrite_node()(state)
        assert out == {"draft": "Dạ anh Tám, phần giá để chủ tiệm báo anh nhé.",
                       "original_draft": "Chị hỏi chủ tiệm giúp em nhé.", "rewritten": True}
        prompt = model.calls[0][0].content
        assert "pronoun" in prompt and "repeat" in prompt and "Câu trước." in prompt
        assert model.kwargs["tags"] == [REWRITE_TAG] and model.kwargs["streaming"] is False

    async def test_model_failure_keeps_the_draft_and_marks_rewritten(self, patch_model):
        patch_model("", fail=True)
        out = await make_rewrite_node()(a_state("Chị ơi.", violations=["pronoun"]))
        assert out == {"rewritten": True}

    async def test_rewrite_tag_is_never_respond(self):
        assert REWRITE_TAG != "respond"
