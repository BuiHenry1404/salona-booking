"""Bộ phân loại câu trả lời xác nhận + `decide` (lưới code trước, model sau).

Model giả thay `reply.build_chat_model` — không gọi Azure.
"""
import asyncio
from datetime import datetime

import pytest

from app.agents.booking_graph import reply
from app.agents.booking_graph.reply import ReplyDecision, classify_reply, decide
from app.core.clock import TZ

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
PROPOSAL = {"kind": "book", "start_at": datetime(2026, 10, 20, 15, 0, tzinfo=TZ).isoformat()}
QUESTION = "Dạ em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho chị Lan, chị chốt giúp em nhé?"


class FakeStructured:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def ainvoke(self, messages, *a, **kw):
        self.calls.append(messages)
        outcome = self.outcome
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome


class FakeModel:
    def __init__(self, outcome):
        self.structured = FakeStructured(outcome)
        self.schema = None

    def with_structured_output(self, schema, **kw):
        self.schema = schema
        return self.structured


def install(monkeypatch, outcome):
    built = {}

    def fake_build(**kwargs):
        built.update(kwargs)
        built["model"] = FakeModel(outcome)
        return built["model"]

    monkeypatch.setattr(reply, "build_chat_model", fake_build)
    return built


async def test_veto_short_circuits_the_model(monkeypatch):
    built = install(monkeypatch, AssertionError("model must not be called"))
    assert await decide("dạ ko em", PROPOSAL, QUESTION, NOW) == "reject"
    assert "model" not in built


async def test_veto_wins_even_when_the_model_would_approve(monkeypatch):
    built = install(monkeypatch, ReplyDecision(decision="approve"))
    sentence = "Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc"
    assert await decide(sentence, PROPOSAL, QUESTION, NOW) == "reject"
    assert "model" not in built


async def test_model_approve_passes_through_when_no_veto(monkeypatch):
    built = install(monkeypatch, ReplyDecision(decision="approve"))
    assert await decide("ừ", PROPOSAL, QUESTION, NOW) == "approve"
    assert built["model"].schema is ReplyDecision


@pytest.mark.parametrize("label", ["reject", "change", "unclear"])
async def test_other_labels_pass_through(monkeypatch, label):
    install(monkeypatch, ReplyDecision(decision=label))
    assert await decide("cho cô dùng thử", PROPOSAL, QUESTION, NOW) == label


async def test_timeout_is_unclear(monkeypatch):
    async def slow():
        await asyncio.sleep(9)
        return ReplyDecision(decision="approve")

    install(monkeypatch, slow)
    monkeypatch.setattr(reply, "CLASSIFY_TIMEOUT_SECONDS", 0.05)
    assert await classify_reply(PROPOSAL, QUESTION, "ừ") == "unclear"


async def test_error_is_unclear(monkeypatch):
    install(monkeypatch, RuntimeError("azure 500"))
    assert await classify_reply(PROPOSAL, QUESTION, "ừ") == "unclear"


async def test_building_the_model_failing_is_unclear(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("missing LLM keys")

    monkeypatch.setattr(reply, "build_chat_model", boom)
    assert await classify_reply(PROPOSAL, QUESTION, "ừ") == "unclear"


async def test_a_non_decision_result_is_unclear(monkeypatch):
    install(monkeypatch, None)
    assert await classify_reply(PROPOSAL, QUESTION, "ừ") == "unclear"


async def test_classifier_model_is_tagged_classify_and_not_streaming(monkeypatch):
    built = install(monkeypatch, ReplyDecision(decision="approve"))
    await classify_reply(PROPOSAL, QUESTION, "ừ")
    assert built["tags"] == ["classify"]
    assert "respond" not in built["tags"]
    assert built["streaming"] is False
    assert built["temperature"] == 0.0


async def test_the_model_sees_proposal_question_and_reply(monkeypatch):
    built = install(monkeypatch, ReplyDecision(decision="approve"))
    await classify_reply(PROPOSAL, QUESTION, "ừ em")
    messages = built["model"].structured.calls[0]
    text = "\n".join(str(m.content) for m in messages)
    assert QUESTION in text and "ừ em" in text
    assert "Thứ Ba 20/10, 3 giờ chiều" in text          # giờ đọc được
    assert "2026-10-20T15:00:00+07:00" in text


async def test_timeout_matches_the_other_llm_side_calls():
    assert reply.CLASSIFY_TIMEOUT_SECONDS == 8


# --- lỗi live 4: "u doi di em" với đề xuất DỜI bị đọc thành `change` ----------

MOVE = {"kind": "move", "start_at": datetime(2026, 10, 20, 16, 0, tzinfo=TZ).isoformat()}
CANCEL = {"kind": "cancel", "start_at": datetime(2026, 10, 20, 15, 0, tzinfo=TZ).isoformat()}


@pytest.mark.parametrize("proposal", [MOVE, CANCEL])
def test_describe_move_says_go_ahead_is_approve(proposal):
    """Không dấu, "doi" vừa là "dời" (đồng ý dời) vừa là "đổi". Một câu tiếng
    Anh (không ví dụ tiếng Việt — bẫy #15/#16): giục làm đúng việc đó bằng
    chính động từ của nó là đồng ý."""
    text = reply._describe(proposal)
    assert "go ahead" in text
    assert "own verb" in text
    assert "means approve" in text


def test_describe_book_has_no_go_ahead_sentence():
    assert "go ahead" not in reply._describe(PROPOSAL)


async def test_rendered_classifier_prompt_carries_the_go_ahead_rule(monkeypatch):
    built = install(monkeypatch, ReplyDecision(decision="approve"))
    await classify_reply(MOVE, "Dạ chị dời lịch sang Thứ Ba 20/10, 4 giờ chiều nha?", "u doi di em")
    human = built["model"].structured.calls[0][-1].content
    assert human.startswith("Proposal: move the customer's existing appointment")
    assert "own verb" in human and "means approve" in human
    assert human.rstrip().endswith("Customer's reply: u doi di em")
    # Không câu ví dụ tiếng Việt nào trong phần mô tả (trước dòng câu hỏi bot).
    description = human.split("\nReceptionist's last message:")[0]
    assert "dời" not in description and "doi" not in description


# Gọi Azure THẬT — chạy tay: `pytest tests/test_reply_classify.py -m llm -v`.
@pytest.mark.llm
@pytest.mark.parametrize("proposal,text,expected", [
    (MOVE, "u doi di em", "approve"),
    (MOVE, "ừ dời đi em", "approve"),
    (CANCEL, "ừ hủy đi em", "approve"),
    (CANCEL, "u huy di em", "approve"),
    (MOVE, "doi qua 5 gio di em", "change"),
])
async def test_go_ahead_with_the_actions_own_verb_is_approve_llm(proposal, text, expected):
    question = "Dạ chị đồng ý giúp em nha?"
    assert await classify_reply(proposal, question, text) == expected
