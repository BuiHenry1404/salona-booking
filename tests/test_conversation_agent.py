"""Task 4.2: `conversation.py` — `render_brief`/`respond`, Conversation Agent
(§8.3 của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Mọi test dùng `ScriptedChatModel` (`tests/fakes.py`) qua
`monkeypatch.setattr("app.agents.booking.conversation.build_chat_model", ...)`
— không gọi Azure.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking.conversation import (
    RESPOND_TAG,
    RESPOND_TIMEOUT_SECONDS,
    render_brief,
    respond,
)
from app.agents.booking.context import UNTRUSTED
from app.agents.booking.prompts.conversation import CONVERSATION_PROMPT
from app.agents.booking.schemas import ClarifyRequest, Fact, ReplyBrief
from tests.fakes import Hang, scripted

BUILD_CHAT_MODEL = "app.agents.booking.conversation.build_chat_model"
CUSTOMER_WORDS = "chi muon doi lich sang mai luc ba gio chieu"


def make_brief(**overrides) -> ReplyBrief:
    defaults = dict(
        kind="answer",
        address="chi Lan",
        facts=[Fact(key="open", spoken="tam gio sang", iso="08:00")],
        ask=None,
        must_say=["em giu lich luc ba gio chieu mai cho chi nhe"],
        must_not_claim=["booked"],
        then=None,
        previous_reply=None,
        tone_context="first_time",
        digest_text="",
        staff_note=None,
        now_local=datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc),
        fallback="Da chi Lan, em chua ghi duoc, chi nhan lai gium em nhe.",
    )
    defaults.update(overrides)
    return ReplyBrief(**defaults)


# ---------------------------------------------------------------------------
# respond() — gọi model
# ---------------------------------------------------------------------------


async def test_respond_uses_a_streaming_model_tagged_respond(monkeypatch):
    captured: dict = {}

    def fake_build_chat_model(**kwargs):
        captured.update(kwargs)
        return scripted([AIMessage(content="Da chi Lan oi.")])

    monkeypatch.setattr(BUILD_CHAT_MODEL, fake_build_chat_model)

    draft, source = await respond(make_brief(), [], CUSTOMER_WORDS)

    assert source == "llm"
    assert draft == "Da chi Lan oi."
    assert captured["tags"] == [RESPOND_TAG]
    assert captured["streaming"] is True
    assert captured["max_retries"] == 0
    # Chủ dự án 08/11: bật reasoning thấp — model không suy luận chép trơ dòng
    # must_say làm cả câu trả lời (lượt chạy thật 08/11).
    assert captured["reasoning_effort"] == "low"


async def test_customer_text_sits_only_under_untrusted_label(monkeypatch):
    model = scripted([AIMessage(content="Da chi oi.")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    await respond(make_brief(), [], CUSTOMER_WORDS)

    sent = model.calls[0]
    system_message = sent[0]
    assert CUSTOMER_WORDS not in system_message.content

    contents = [m.content for m in sent]
    occurrences = sum(c.count(CUSTOMER_WORDS) for c in contents)
    assert occurrences == 1

    human_with_question = next(m for m in sent if isinstance(m, HumanMessage) and CUSTOMER_WORDS in m.content)
    label_pos = human_with_question.content.index(UNTRUSTED)
    question_pos = human_with_question.content.index(CUSTOMER_WORDS)
    assert question_pos > label_pos


async def test_history_window_is_passed_before_the_brief(monkeypatch):
    model = scripted([AIMessage(content="Da chi oi.")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    history = [HumanMessage(content="hom qua chi hoi gio mo cua"), AIMessage(content="Da tam gio sang chi nhe.")]

    await respond(make_brief(), history, CUSTOMER_WORDS)

    sent = model.calls[0]
    assert sent[0].content == CONVERSATION_PROMPT
    assert sent[1] is history[0]
    assert sent[2] is history[1]
    assert isinstance(sent[-1], HumanMessage)
    assert CUSTOMER_WORDS in sent[-1].content


async def test_model_error_returns_the_fallback_as_code(monkeypatch):
    model = scripted([RuntimeError("boom"), RuntimeError("boom again")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    brief = make_brief()
    draft, source = await respond(brief, [], CUSTOMER_WORDS)

    assert source == "code"
    assert draft == brief.fallback
    # RuntimeError khong phai loi tam thoi -> khong thu lai, chi mot lan goi.
    assert len(model.calls) == 1


async def test_timeout_returns_the_fallback(monkeypatch):
    monkeypatch.setattr("app.agents.booking.conversation.RESPOND_TIMEOUT_SECONDS", 0.05)
    model = scripted([Hang(seconds=5), Hang(seconds=5)])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    brief = make_brief()
    draft, source = await respond(brief, [], CUSTOMER_WORDS)

    assert source == "code"
    assert draft == brief.fallback
    # Timeout la loi tam thoi (is_transient) -> thu lai dung mot lan.
    assert len(model.calls) == 2


async def test_empty_output_returns_the_fallback(monkeypatch):
    model = scripted([AIMessage(content="   ")])
    monkeypatch.setattr(BUILD_CHAT_MODEL, lambda **kw: model)

    brief = make_brief()
    draft, source = await respond(brief, [], CUSTOMER_WORDS)

    assert source == "code"
    assert draft == brief.fallback
    # Rong khong phai loi -> khong thu lai.
    assert len(model.calls) == 1


# ---------------------------------------------------------------------------
# render_brief()
# ---------------------------------------------------------------------------


def test_render_brief_prints_must_say_verbatim_and_every_fact_spoken():
    brief = make_brief(
        facts=[
            Fact(key="held_at", spoken="ba gio chieu mai", iso="2026-11-07T15:00:00+07:00"),
            Fact(key="note", text="toc ngan"),
        ],
        must_say=["em giu lich ba gio chieu mai cho chi nhe"],
        ask=ClarifyRequest(field="service", hint="lam toc hay lam mong"),
        then="Sau gio nay em se hoi tiep bon gio",
        previous_reply="Da chi oi em dang xu ly a",
        staff_note="khach lon tuoi, noi cham",
        digest_text="chi Lan hay lam mong gel",
    )

    rendered = render_brief(brief)

    for line in brief.must_say:
        assert line in rendered
    for fact in brief.facts:
        if fact.spoken is not None:
            assert fact.spoken in rendered
    assert "service" in rendered
    assert "lam toc hay lam mong" in rendered
    assert "booked" in rendered
    assert brief.then in rendered
    assert brief.previous_reply in rendered
    assert brief.staff_note in rendered
    assert brief.digest_text in rendered
    assert brief.now_local.isoformat() in rendered


def test_render_brief_handles_empty_optional_fields():
    brief = make_brief(must_say=[], ask=None, must_not_claim=[], then=None, previous_reply=None)

    rendered = render_brief(brief)

    assert "Must mention: none" in rendered
    assert "Ask: none" in rendered
    assert "Never claim: none" in rendered
    assert "Then: none" in rendered
    assert "Previous reply: none" in rendered


def test_conversation_prompt_is_english():
    assert CONVERSATION_PROMPT.isascii()
