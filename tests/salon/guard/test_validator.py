"""`guard/validator.py`: the model's plain-text reply checked ONLY against
extracted facts (times, prices, catalogue service names, phone digits, "?",
character script, tool calls) — guard rebuild, Task 7. No grammar word lists.
"""
from __future__ import annotations

from app.agents.salon.guard import templates, validator
from app.agents.salon.guard.planner import ReplyPlan, build_plan
from app.agents.salon.guard.results import TurnResults
from app.agents.salon.guard.validator import CODES, strip_tool_text, validate
from tests.salon.guard.builders import (
    CAT,
    GEL,
    TODAY,
    a_snapshot,
    at,
    pending_at,
    proposal_fact,
    service_fact,
    time_fact,
    turn_results,
)
from tests.salon.guard.plan_cases import PLAN_CASES, RESULTS, SHOP_PHONE


def _codes(text: str, results: TurnResults, plan: ReplyPlan | None = None, **kwargs) -> list[str]:
    return validate(text, plan or build_plan(results), results, **kwargs).codes


def _taken_results() -> TurnResults:
    return RESULTS["taken_with_session_offer"]


# -- offer / taken -------------------------------------------------------------


def test_offer_reply_passes():
    text = "Còn trống 4 giờ hoặc 5 giờ chiều mai, chị chọn giờ nào ạ?"
    assert _codes(text, _taken_results()) == []


def test_taken_mentioned():
    results = _taken_results()
    plan = build_plan(results)
    verdict = validate("Chị chọn 3 giờ hay 4 giờ chiều mai ạ?", plan, results)
    assert "taken_mentioned" in verdict.codes
    assert "plan_missing" in verdict.codes
    assert verdict.missing == [{"act": "offer", "times": ["4 giờ chiều mai", "5 giờ chiều mai"]}]


def test_taken_clock_equal_to_an_act_clock_is_allowed():
    # 3 giờ chiều mai is taken; 3 giờ chiều thứ Tư (another day) is free.
    other_day = at(15, day=TODAY.replace(day=11), spoken="Thứ Tư 11/11, 3 giờ chiều")
    results = turn_results(turn_facts=[time_fact("taken", at(15)), time_fact("free", other_day)])
    plan = build_plan(results)
    assert plan.taken_clocks == [(15, 0)]
    assert (15, 0) in plan.act_clocks()
    text = "Dạ còn trống 3 giờ chiều thứ Tư 11/11, chị chọn giờ đó không ạ?"
    assert _codes(text, results, plan) == []


# -- plan_missing ----------------------------------------------------------------


def test_plan_missing_service_name():
    results = RESULTS["done_with_services"]
    plan = build_plan(results)
    assert plan.acts[0].act == "done" and plan.acts[0].service_names == ["Sơn gel"]
    verdict = validate("Dạ em đã đặt xong lịch 4 giờ chiều mai cho chị ạ.", plan, results)
    assert verdict.codes == ["plan_missing"]
    assert verdict.missing == [{"act": "done", "kind": "book", "at": "4 giờ chiều mai",
                                "services": ["Sơn gel"]}]


def test_missing_ask_is_reported_as_ask():
    results = RESULTS["ask_combined"]
    verdict = validate("Dạ chị muốn đặt lúc nào ạ.", build_plan(results), results)
    assert verdict.codes == ["missing_question"]
    assert verdict.missing == [{"ask": {"fields": ["day", "time"], "options": []}}]


# -- unknown_time ----------------------------------------------------------------


def test_unknown_time():
    text = "Còn trống 4 giờ, 5 giờ hoặc 9 giờ tối mai, chị chọn giờ nào ạ?"
    assert _codes(text, _taken_results()) == ["unknown_time"]


def test_customer_typed_time_is_valid():
    snapshot = RESULTS["taken_with_session_offer"].snapshot
    results = turn_results(snapshot=snapshot, turn_facts=[time_fact("taken", at(15))],
                           question="8 giờ tối mai được không em")
    text = "Dạ 8 giờ tối thì chưa có, còn trống 4 giờ hoặc 5 giờ chiều mai, chị chọn giờ nào ạ?"
    assert _codes(text, results) == []


def test_context_shop_hours_valid_without_act():
    # Greeting turn: no act, but the context block gives shop hours 08:00-19:00.
    assert _codes("Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ.", RESULTS["greeting"]) == []


# -- unknown_price ---------------------------------------------------------------


def test_unknown_price():
    results = turn_results(turn_facts=[proposal_fact(at(16), service_ids=[GEL.id])])
    template = "Dạ em giữ chỗ sơn gel lúc 4 giờ chiều mai, giá khoảng {} nghìn, chị đồng ý không ạ?"
    assert _codes(template.format(200), results) == ["unknown_price"]
    assert _codes(template.format(150), results) == []


def test_price_act_needs_an_amount_in_range():
    results = RESULTS["identified_no_booking"]
    assert _codes("Dạ sơn gel giá 120 đến 150 nghìn ạ.", results) == []
    verdict = validate("Dạ sơn gel giá 200 nghìn ạ.", build_plan(results), results)
    assert verdict.codes == ["plan_missing", "unknown_price"]
    assert verdict.missing == [{"act": "price", "service": "Sơn gel",
                                "price": "120 đến 150 nghìn"}]


# -- wrong_service ---------------------------------------------------------------


def test_wrong_service():
    results = turn_results(turn_facts=[proposal_fact(at(14), service_ids=[CAT.id])])
    codes = _codes("em giữ chỗ sơn gel 2 giờ chiều mai", results)
    assert "wrong_service" in codes
    assert "plan_missing" in codes


def test_name_inside_a_longer_turn_name_is_not_wrong_service():
    results = turn_results(turn_facts=[proposal_fact(at(14), service_ids=[CAT.id])])
    results = results.model_copy(update={"shop_service_names": [*results.shop_service_names,
                                                                "Cắt tóc"]})
    text = "Dạ em giữ chỗ cắt tóc nữ lúc 2 giờ chiều mai, chị đồng ý không ạ?"
    assert _codes(text, results) == []
    # "Cắt tóc" on its own is another service of the shop.
    text = "Dạ em giữ chỗ cắt tóc nữ lúc 2 giờ chiều mai, có cần cắt tóc thêm không ạ?"
    assert _codes(text, results) == ["wrong_service"]


# -- missing_question ------------------------------------------------------------


def test_missing_question():
    results = turn_results(turn_facts=[proposal_fact(at(16), service_ids=[GEL.id])])
    assert _codes("Dạ em giữ chỗ sơn gel lúc 4 giờ chiều mai cho chị ạ.", results) == [
        "missing_question"]
    assert _codes("Còn trống 4 giờ hoặc 5 giờ chiều mai ạ.", _taken_results()) == [
        "missing_question"]
    assert _codes("Dạ chị muốn ngày nào, mấy giờ ạ.", RESULTS["ask_combined"]) == [
        "missing_question"]


# -- phone -----------------------------------------------------------------------


def test_phone_rules():
    failed = RESULTS["failed_no_nearest"]
    assert build_plan(failed).phone == SHOP_PHONE
    assert _codes("Chị ơi, 3 giờ chiều mai em chưa ghi được ạ.", failed) == ["missing_phone"]
    assert _codes(f"Chị ơi, 3 giờ chiều mai em chưa ghi được, chị gọi số {SHOP_PHONE} nhé ạ.",
                  failed) == []
    greeting = turn_results(shop_phone=SHOP_PHONE)
    assert build_plan(greeting).phone is None
    assert _codes(f"Dạ chị gọi tiệm số {SHOP_PHONE} nhé ạ.", greeting) == ["phone_not_allowed"]


def test_phone_and_date_are_not_prices():
    failed = RESULTS["failed_no_nearest"]
    codes = _codes("Dạ 3 giờ chiều mai em chưa ghi được, chị gọi số tiệm 0912 345 678, "
                   "lịch ngày 14/11 ạ.", failed)
    assert "unknown_price" not in codes
    assert "unknown_time" not in codes
    assert "missing_phone" not in codes


# -- pending / empty / script / repeat / tool call --------------------------------


def test_pending_undecided():
    results = turn_results(snapshot=a_snapshot(pending=pending_at(at(11), service_ids=[CAT.id])))
    assert results.pending_undecided
    assert _codes("Dạ chị cần gì thêm không ạ?", results) == ["pending_undecided"]


def test_empty_after_strip():
    verdict = validate("decide_pending()", build_plan(RESULTS["greeting"]), RESULTS["greeting"])
    assert verdict.text == ""
    assert verdict.codes == ["empty"]


def test_strip_tool_text_drops_tools_json_and_bold():
    assert strip_tool_text('Dạ **4 giờ chiều mai** còn trống ạ {"a": 1} `find_free_slots`.') == (
        "Dạ 4 giờ chiều mai còn trống ạ.")
    assert strip_tool_text("Dạ vâng ạ.\nEm chào chị.") == "Dạ vâng ạ.\nEm chào chị."


def test_foreign_script():
    assert _codes("Dạ chị Լան cần gì ạ?", RESULTS["greeting"]) == ["foreign_script"]


def test_repeat():
    previous = "Dạ tiệm mở cửa từ 8 giờ sáng đến 7 giờ tối ạ."
    results = turn_results(previous=["Dạ chào chị.", previous])
    assert _codes(previous, results) == ["repeat"]
    # Older than the last 2 replies: not a repeat.
    results = turn_results(previous=[previous, "Dạ chào chị.", "Dạ vâng ạ."])
    assert _codes(previous, results) == []


def test_bad_tool_call_first():
    codes = _codes("", RESULTS["greeting"], invalid_tool_call=True)
    assert codes[0] == "bad_tool_call"
    assert codes == ["bad_tool_call", "empty"]


def test_codes_follow_codes_order():
    results = turn_results(snapshot=a_snapshot(pending=pending_at(at(11), service_ids=[CAT.id])),
                           turn_facts=[service_fact(GEL, identified=True)])
    codes = _codes("Dạ Լ 9 giờ tối ạ.", results)
    assert codes == [code for code in CODES if code in codes]
    assert codes == ["pending_undecided", "plan_missing", "unknown_time", "foreign_script"]


# -- word lists / templates --------------------------------------------------------


def _has_vietnamese_letter(value: str) -> bool:
    return any(ch.isalpha() and not ch.isascii() for ch in value)


def _strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.items() for part in item for s in _strings_in(part)]
    if isinstance(value, (set, frozenset, tuple, list)):
        return [s for item in value for s in _strings_in(item)]
    return []


def test_no_word_lists():
    for name, value in vars(validator).items():
        if name.startswith("__") or not isinstance(value, (set, frozenset, tuple, list, dict)):
            continue
        offenders = [s for s in _strings_in(value) if _has_vietnamese_letter(s)]
        assert not offenders, f"{name} holds Vietnamese words: {offenders}"


def test_template_body_passes_validator():
    for name, results, plan in PLAN_CASES:
        # An empty plan has no body (""); its reply is `compose` = GENERIC_FALLBACK.
        text = (templates.body(plan, results.address) if not plan.is_empty()
                else templates.compose(plan, results.address))
        verdict = validate(text, plan, results)
        assert verdict.codes == [], f"{name}: {text!r} -> {verdict.codes} {verdict.missing}"
