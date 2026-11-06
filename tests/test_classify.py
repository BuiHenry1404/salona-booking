"""Task 4.4: `classify.py` — `classify_only`, bộ phân loại rẻ cho lượt
"sạch" (R4, §4/§5 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Mọi test dùng `ScriptedChatModel` (`tests/fakes.py`) qua
`monkeypatch.setattr("app.agents.booking.classify.build_chat_model", ...)`
— không gọi Azure.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking.classify import CLASSIFY_TAG, ClassifyOut, classify_only
from app.agents.booking.context import UNTRUSTED, AppointmentRef
from app.agents.booking.prompts.classify import CLASSIFY_PROMPT
from tests.factories import make_ctx
from tests.fakes import scripted

BUILD_CHAT_MODEL = "app.agents.booking.classify.build_chat_model"
CUSTOMER_WORDS = "chao em, tiem co lam mi khong"


async def test_social_route_from_scripted_model(monkeypatch):
    model = scripted([ClassifyOut(route="social")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    route = await classify_only(make_ctx(), CUSTOMER_WORDS)

    assert route == "social"


async def test_errors_fall_back_to_business(monkeypatch):
    model = scripted([RuntimeError("boom")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    route = await classify_only(make_ctx(), CUSTOMER_WORDS)

    assert route == "business"


async def test_classify_uses_the_lite_render_without_upcoming_ids(monkeypatch):
    model = scripted([ClassifyOut(route="business")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    ctx = make_ctx(upcoming=[
        AppointmentRef(id="deadbeef00000000", start_at=make_ctx().now_local, spoken="ba gio chieu mai"),
    ])

    await classify_only(ctx, CUSTOMER_WORDS)

    sent = model.calls[0]
    contents = "\n".join(m.content for m in sent)
    assert "deadbeef00000000" not in contents
    assert "Queued requests:" in contents
    assert "Upcoming appointments:" not in contents


async def test_classify_model_is_not_tagged_respond(monkeypatch):
    captured: dict = {}

    def fake_build_chat_model(**kwargs):
        captured.update(kwargs)
        return scripted([ClassifyOut(route="social")])

    monkeypatch.setattr(BUILD_CHAT_MODEL, fake_build_chat_model)

    await classify_only(make_ctx(), CUSTOMER_WORDS)

    assert captured["tags"] == [CLASSIFY_TAG]
    assert "respond" not in captured["tags"]
    assert captured["streaming"] is False


def test_classify_prompt_is_english():
    assert CLASSIFY_PROMPT.isascii()


async def test_customer_text_sits_only_under_untrusted_label(monkeypatch):
    model = scripted([ClassifyOut(route="social")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    await classify_only(make_ctx(), CUSTOMER_WORDS)

    sent = model.calls[0]
    assert isinstance(sent[0], SystemMessage)
    assert CUSTOMER_WORDS not in sent[0].content

    contents = [m.content for m in sent]
    occurrences = sum(c.count(CUSTOMER_WORDS) for c in contents)
    assert occurrences == 1

    human = next(m for m in sent if isinstance(m, HumanMessage) and CUSTOMER_WORDS in m.content)
    assert human.content.index(UNTRUSTED) < human.content.index(CUSTOMER_WORDS)
