"""PhraseMiddleware (`before_agent`): câu chốt thay cho lượt agent, rồi Guard kiểm.

Agent thật (`build_agent(..., user=USER)` qua `make_agent_node`), model agent
giả NÉM nếu bị gọi — lượt câu chốt không được chạm tới model hay tool.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph import phrase
from app.agents.booking_graph.agents import build_agent, make_agent_node
from app.agents.booking_graph.middleware.phrase import PhraseMiddleware
from app.models.user import User
from tests.fakes import Hang, scripted

USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")
WHEN = "Thứ Ba 15/9, 9 giờ sáng"
FACT = {"kind": "booked", "when": WHEN, "note": "cắt tóc", "address": "anh Tám", "error": None}
FALLBACK = f"Xong rồi ạ. Hẹn gặp anh Tám {WHEN} nhé."


def must_not_run():
    return scripted([RuntimeError("model agent không được gọi ở lượt câu chốt")])


def node_for(model):
    return make_agent_node(build_agent("p", [], model=model, user=USER))


def a_state(**extra):
    return {"messages": [HumanMessage("dời sang mai nha"), AIMessage("Dạ mai 9 giờ sáng được không anh?"),
                         HumanMessage("ừ")],
            "context_block": "Gọi khách là: anh Tám.", "previous_replies": [],
            "customer_text": "ừ", "confirm_fact": FACT, "fallback": FALLBACK, **extra}


@pytest.fixture
def phrase_model(monkeypatch):
    def _install(*replies):
        # Không tag sẵn: tag phải do `write_phrase` xin lúc dựng model (F10).
        model = scripted(list(replies), tags=())

        def build(**kw):
            model.tags = list(kw.get("tags") or [])
            return model

        monkeypatch.setattr(phrase, "build_chat_model", build)
        return model
    return _install


async def test_phrase_runs_instead_of_the_model_and_guard_checks_it(phrase_model):
    text = f"Dạ anh Tám, em giữ cho anh {WHEN} rồi nhé."
    written = phrase_model(AIMessage(text))
    agent_model = must_not_run()

    out = await node_for(agent_model)(a_state())

    assert agent_model.calls == []
    assert len(written.calls) == 1
    assert out["answer"] == text and out["answer_source"] == "llm" and out["violations"] == []
    # Lượt câu chốt không đụng tới đề xuất (clarify phải giữ nó cho confirm).
    assert "proposal_id" not in out


async def test_phrase_missing_when_falls_back_to_code_sentence(phrase_model):
    phrase_model(AIMessage("Dạ anh Tám, em đặt xong rồi nhé."))
    out = await node_for(must_not_run())(a_state())
    assert out["answer"] == FALLBACK and out["answer_source"] == "code"
    assert "when" in out["violations"]


async def test_phrase_timeout_ships_fallback(phrase_model, monkeypatch):
    phrase_model(Hang(5, AIMessage("quá muộn")))
    monkeypatch.setattr(phrase, "PHRASE_TIMEOUT_SECONDS", 0.05)
    out = await node_for(must_not_run())(a_state())
    assert out["answer"] == FALLBACK and out["answer_source"] == "code"


async def test_clarify_is_the_code_sentence_with_no_llm_call(phrase_model):
    written = phrase_model()
    ask = f"Dạ anh Tám chốt giúp em lịch {WHEN} nha, đúng không ạ?"
    fact = {"kind": "clarify", "when": WHEN, "note": None, "address": "anh Tám", "error": None}
    out = await node_for(must_not_run())(a_state(confirm_fact=fact, fallback=ask))
    assert written.calls == []
    assert out["answer"] == ask and out["answer_source"] == "code"
    assert "proposal_id" not in out


async def test_no_confirm_fact_runs_the_agent_normally(phrase_model):
    written = phrase_model()
    agent_model = scripted([AIMessage("Dạ anh Tám, tiệm mở cửa 9 giờ sáng ạ.")])
    out = await node_for(agent_model)(a_state(confirm_fact=None, fallback=None))
    assert written.calls == [] and len(agent_model.calls) == 1
    assert out["answer"] == "Dạ anh Tám, tiệm mở cửa 9 giờ sáng ạ."
    assert out["proposal_id"] is None


async def test_before_agent_returns_none_without_a_fact():
    assert await PhraseMiddleware().abefore_agent({"messages": []}, None) is None


async def test_phrase_tokens_stream_with_the_respond_tag(phrase_model):
    """Câu chốt LLM stream như A (bẫy #8): token mang tag `respond`."""
    text = f"Dạ anh Tám, em giữ cho anh {WHEN} rồi nhé."
    phrase_model(AIMessage(text))
    agent = build_agent("p", [], model=must_not_run(), user=USER)
    tagged = []
    async for ev in agent.astream_events(a_state(), version="v2"):
        if ev["event"] == "on_chat_model_stream":
            tagged.append((tuple(ev.get("tags") or ()), ev["data"]["chunk"].content))
    assert tagged and all("respond" in tags for tags, _ in tagged)
    assert "".join(c for _, c in tagged) == text
