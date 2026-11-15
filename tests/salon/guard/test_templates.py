"""`guard/templates.py`: Vietnamese sentences built from a `ReplyPlan` (guard
rebuild, Task 6). `button_reply` keeps the exact sentences the old
`guard/fallback.py::button_reply` gave (captured before Task 8 deleted it).
"""
from __future__ import annotations

from app.agents.salon.guard.planner import PlanAct, ReplyPlan, build_plan
from app.agents.salon.guard.templates import (
    GENERIC_FALLBACK,
    body,
    button_reply,
    compose,
    taken_sentence,
)
from app.agents.salon.shared.text_scan import split_sentences
from tests.salon.guard.builders import TODAY, at, outcome
from tests.salon.guard.plan_cases import PLAN_CASES, RESULTS, SHOP_PHONE

ADDRESS = "chị Lan"


def _plan(name: str) -> ReplyPlan:
    return build_plan(RESULTS[name])


def test_compose_prefixes_taken():
    plan = _plan("taken_with_session_offer")
    assert compose(plan, ADDRESS) == (
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. "
        "Dạ còn trống 4 giờ chiều mai hoặc 5 giờ chiều mai, "
        f"{ADDRESS} chọn giờ nào ạ?"
    )


def test_field_question_combines_fields():
    plan = _plan("ask_combined")
    text = compose(plan, ADDRESS)
    assert text.endswith("?")
    assert "ngày nào" in text
    assert "mấy giờ" in text


def test_price_sentence_is_spoken():
    plan = ReplyPlan(acts=[PlanAct(act="price", fields={"service": "Sơn gel",
                                                        "price": "120 đến 150 nghìn"})])
    assert compose(plan, ADDRESS) == "Dạ sơn gel giá 120 đến 150 nghìn ạ."


def test_taken_only_plan():
    plan = ReplyPlan(taken=["3 giờ chiều mai"])
    assert compose(plan, ADDRESS) == "Dạ 3 giờ chiều mai đã có khách rồi ạ."
    assert taken_sentence(plan, ADDRESS) == "Dạ 3 giờ chiều mai đã có khách rồi ạ."
    assert body(plan, ADDRESS) == ""


def test_failed_with_phone():
    plan = _plan("failed_no_nearest")
    assert compose(plan, ADDRESS) == (
        "Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, "
        f"{ADDRESS} gọi giúp em số {SHOP_PHONE} nhé ạ."
    )


def test_failed_with_nearest_has_no_phone():
    plan = _plan("failed_with_nearest")
    text = compose(plan, ADDRESS)
    assert "vừa có người đặt mất rồi" in text
    assert SHOP_PHONE not in text
    assert "Còn trống 4 giờ chiều mai hoặc 5 giờ chiều mai" in text


def test_failed_twice_second_act_is_also_failed():
    plan = _plan("failed_twice")
    text = compose(plan, ADDRESS)
    assert "em cũng chưa ghi được" in text


def test_empty_plan_is_generic():
    plan = ReplyPlan()
    assert plan.is_empty()
    assert compose(plan, ADDRESS) == GENERIC_FALLBACK.format(address=ADDRESS)


def test_button_reply_unchanged_cases():
    """Literal sentences of the old `fallback.button_reply` for the same inputs."""
    approved = outcome(at(16), outcome="approved", kind="book")
    rejected = outcome(at(16), outcome="rejected", kind="book")
    failed_nearest = outcome(at(16), outcome="failed", nearest=[at(17), at(18)])
    failed_no_phone = outcome(at(16), outcome="failed")
    expected = [
        (approved, "Dạ em đã đặt xong 4 giờ chiều mai cho chị Lan ạ."),
        (rejected, "Dạ vâng, em bỏ lịch 4 giờ chiều mai rồi ạ. "
                   "Chị Lan muốn giờ khác thì nhắn em nhé."),
        (failed_nearest, "Dạ 4 giờ chiều mai vừa có người đặt mất rồi ạ. Còn trống 5 giờ chiều "
                         "mai hoặc 6 giờ tối mai, chị Lan nhắn em giờ muốn đặt nhé."),
        (failed_no_phone, "Chị Lan ơi, 4 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em "
                          "số 0912345678 nhé ạ."),
    ]

    for pending_outcome, sentence in expected:
        assert button_reply(pending_outcome, address=ADDRESS, shop_phone=SHOP_PHONE,
                            today=TODAY) == sentence

    assert button_reply(failed_no_phone, address=ADDRESS, shop_phone=None, today=TODAY) == (
        "Dạ 4 giờ chiều mai em chưa ghi được, chị Lan nhắn lại giờ khác giúp em nhé.")


def _starts_upper(sentence: str) -> bool:
    first = next((ch for ch in sentence if ch.isalpha()), "")
    return first == "" or first.isupper()


def test_every_template_sentence_starts_with_a_capital():
    """Controller ruling (Task 8): `{address}` can open a sentence ("chị Lan
    ơi, …"); `body`, `compose` and `taken_sentence` capitalize every sentence."""
    for name, _results, plan in PLAN_CASES:
        for text in (compose(plan, ADDRESS), body(plan, ADDRESS), taken_sentence(plan, ADDRESS)):
            for sentence in split_sentences(text):
                assert _starts_upper(sentence), (name, sentence)
    rejected = ReplyPlan(acts=[PlanAct(act="released", fields={"kind": "book",
                                                               "at": "4 giờ chiều mai"})])
    assert body(rejected, ADDRESS) == (
        "Dạ vâng, em bỏ lịch 4 giờ chiều mai rồi ạ. Chị Lan muốn giờ khác thì nhắn em nhé.")
    failed = build_plan(RESULTS["failed_no_nearest"])
    assert body(failed, ADDRESS).startswith("Chị Lan ơi, ")
