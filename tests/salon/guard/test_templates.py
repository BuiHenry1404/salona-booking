"""`guard/templates.py`: Vietnamese sentences built from a `ReplyPlan` (guard
rebuild, Task 6). `button_reply` is compared against today's
`guard/fallback.py::button_reply` for the same inputs, while that module is
still present (deleted at Task 8) — the only test allowed to import it.
"""
from __future__ import annotations

from app.agents.salon.guard import fallback
from app.agents.salon.guard.planner import PlanAct, PlanAsk, ReplyPlan, build_plan
from app.agents.salon.guard.templates import GENERIC_FALLBACK, body, compose, taken_sentence
from app.agents.salon.guard.templates import button_reply as new_button_reply
from tests.salon.guard.builders import TODAY, at
from tests.salon.guard.plan_cases import RESULTS, SHOP_PHONE

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
    assert compose(plan, ADDRESS) == f"Dạ sơn gel giá 120 đến 150 nghìn ạ."


def test_taken_only_plan():
    plan = ReplyPlan(taken=["3 giờ chiều mai"])
    assert compose(plan, ADDRESS) == "Dạ 3 giờ chiều mai đã có khách rồi ạ."
    assert taken_sentence(plan, ADDRESS) == "Dạ 3 giờ chiều mai đã có khách rồi ạ."
    assert body(plan, ADDRESS) == ""


def test_failed_with_phone():
    plan = _plan("failed_no_nearest")
    assert compose(plan, ADDRESS) == (
        f"{ADDRESS} ơi, 3 giờ chiều mai em chưa ghi được, "
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
    from tests.salon.guard.builders import outcome

    approved = outcome(at(16), outcome="approved", kind="book")
    rejected = outcome(at(16), outcome="rejected", kind="book")
    failed_nearest = outcome(at(16), outcome="failed", nearest=[at(17), at(18)])
    failed_no_phone = outcome(at(16), outcome="failed")

    for pending_outcome in (approved, rejected, failed_nearest, failed_no_phone):
        old = fallback.button_reply(pending_outcome, address=ADDRESS, shop_phone=SHOP_PHONE,
                                    today=TODAY)
        new = new_button_reply(pending_outcome, address=ADDRESS, shop_phone=SHOP_PHONE,
                               today=TODAY)
        assert new == old

    old_no_phone = fallback.button_reply(failed_no_phone, address=ADDRESS, shop_phone=None,
                                         today=TODAY)
    new_no_phone = new_button_reply(failed_no_phone, address=ADDRESS, shop_phone=None,
                                    today=TODAY)
    assert new_no_phone == old_no_phone
