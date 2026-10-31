"""Task 1.5: Resolver trạng thái — bảng luật R0–R5 (§4.3).

Resolver là node đầu tiên mỗi lượt: code thuần, không LLM, không ghi DB,
không từ khoá. Bảng test tham số hoá phủ R1–R5, cộng test riêng cho
`must_announce` (R0, không phải nhánh riêng) và tính không đổi (`resolve`
không được sửa `ctx`).
"""
from __future__ import annotations

import ast
import copy
import inspect

import pytest

from app.agents.booking import resolver as resolver_module
from app.agents.booking.context import OpenQuestion, PendingProposal, UnannouncedOutcome
from app.agents.booking.resolver import ResolveResult, is_short_time_answer, mentions_time_or_number, resolve
from app.agents.booking_graph.button import ButtonReply

from tests.factories import make_ctx


def _pending(**overrides) -> PendingProposal:
    defaults = dict(
        id="aaaaaaaaaaaaaaaaaaaaaaaa",
        kind="book",
        start_at=make_ctx().now_local,
        spoken="9h sáng mai",
        expires_at=make_ctx().now_local,
        unclear_count=0,
    )
    defaults.update(overrides)
    return PendingProposal(**defaults)


def _open_question(**overrides) -> OpenQuestion:
    defaults = dict(field="time", asked_at=make_ctx().now_local, times=1)
    defaults.update(overrides)
    return OpenQuestion(**defaults)


def _unannounced(**overrides) -> UnannouncedOutcome:
    defaults = dict(
        proposal_id="aaaaaaaaaaaaaaaaaaaaaaaa",
        outcome="approved",
        kind="book",
        spoken="9h sáng mai",
    )
    defaults.update(overrides)
    return UnannouncedOutcome(**defaults)


# ---------------------------------------------------------------------------
# Bảng luật R1-R5 (tham số hoá)
# ---------------------------------------------------------------------------


def _ctx_plain():
    return make_ctx()


def _ctx_with_pending():
    return make_ctx(pending=_pending())


def _ctx_with_open_question():
    return make_ctx(open_question=_open_question())


def _ctx_with_queued():
    from app.models.conversation import QueuedRequest

    queued = QueuedRequest(
        queue_id="q1",
        start_at=make_ctx().now_local,
        spoken="9h sáng mai",
        queued_at=make_ctx().now_local,
    )
    return make_ctx(queued_requests=[queued])


RULE_TABLE = [
    pytest.param(
        _ctx_with_pending,
        ButtonReply(decision="yes", proposal_id="aaaaaaaaaaaaaaaaaaaaaaaa", label="Đồng ý"),
        "button",
        "R1",
        False,
        id="button-wins-even-with-pending",
    ),
    pytest.param(
        _ctx_with_pending,
        "9h sáng mai được không em",
        "orchestrate",
        "R2",
        False,
        id="pending-text-to-orchestrate",
    ),
    pytest.param(
        _ctx_with_open_question,
        "3 giờ chiều",
        "orchestrate",
        "R3",
        True,
        id="short-time-answer-to-open-question",
    ),
    pytest.param(
        _ctx_with_open_question,
        "chị muốn đặt lịch cắt tóc vào 3 giờ chiều",
        "orchestrate",
        "R5",
        False,
        id="long-answer-to-open-question-is-r5",
    ),
    pytest.param(
        _ctx_plain,
        "chào em",
        "classify_only",
        "R4",
        False,
        id="plain-chat-without-numbers",
    ),
    pytest.param(
        _ctx_plain,
        "ok",
        "classify_only",
        "R4",
        False,
        id="bare-ok-without-numbers",
    ),
    pytest.param(
        _ctx_plain,
        "thứ năm được không",
        "orchestrate",
        "R5",
        False,
        id="weekday-skips-classify",
    ),
    pytest.param(
        _ctx_plain,
        "mai nhé",
        "orchestrate",
        "R5",
        False,
        id="day-offset-skips-classify",
    ),
    pytest.param(
        _ctx_plain,
        "chị muốn đặt lịch làm tóc lúc 3 giờ",
        "orchestrate",
        "R5",
        False,
        id="nine-word-sentence-with-time",
    ),
    pytest.param(
        _ctx_with_queued,
        "chào em",
        "orchestrate",
        "R5",
        False,
        id="queued-request-disables-r4",
    ),
    pytest.param(
        _ctx_plain,
        "",
        "classify_only",
        "R4",
        False,
        id="empty-string",
    ),
]


@pytest.mark.parametrize("ctx_factory, question, expected_route, expected_rule, expected_hint", RULE_TABLE)
def test_resolve_rule_table(ctx_factory, question, expected_route, expected_rule, expected_hint):
    ctx = ctx_factory()
    result = resolve(ctx, question)
    assert result.route == expected_route
    assert result.rule == expected_rule
    assert result.answers_open_question_hint == expected_hint


def test_unannounced_disables_r4():
    """`unannounced` khác None loại R4 — về R5 dù câu không có giờ/ngày/số."""
    ctx = make_ctx(unannounced=_unannounced())
    result = resolve(ctx, "chào em")
    assert result.route == "orchestrate"
    assert result.rule == "R5"


# ---------------------------------------------------------------------------
# R0: must_announce đi kèm MỌI nhánh, không phải nhánh riêng
# ---------------------------------------------------------------------------


def test_must_announce_true_with_unannounced_on_any_rule():
    ctx = make_ctx(unannounced=_unannounced())
    result = resolve(ctx, "chào em")
    assert result.must_announce is True
    assert result.rule == "R5"  # unannounced cũng loại R4


def test_must_announce_true_with_unannounced_even_on_button_path():
    ctx = make_ctx(unannounced=_unannounced())
    button = ButtonReply(decision="no", proposal_id="aaaaaaaaaaaaaaaaaaaaaaaa", label="Không")
    result = resolve(ctx, button)
    assert result.must_announce is True
    assert result.rule == "R1"


def test_must_announce_false_without_unannounced():
    ctx = make_ctx()
    result = resolve(ctx, "chào em")
    assert result.must_announce is False


# ---------------------------------------------------------------------------
# resolve không được sửa ctx
# ---------------------------------------------------------------------------


def test_resolve_does_not_mutate_ctx():
    ctx = make_ctx(pending=_pending(), open_question=_open_question())
    before = copy.deepcopy(ctx)
    resolve(ctx, "3 giờ chiều")
    assert ctx == before


def test_resolve_result_is_well_typed():
    ctx = make_ctx()
    result = resolve(ctx, "chào em")
    assert isinstance(result, ResolveResult)


# ---------------------------------------------------------------------------
# Hàm phụ trợ
# ---------------------------------------------------------------------------


def test_is_short_time_answer_true_for_short_clock_mention():
    assert is_short_time_answer("3 giờ chiều") is True


def test_is_short_time_answer_false_for_long_sentence_even_with_time():
    assert is_short_time_answer("chị muốn đặt lịch cắt tóc vào 3 giờ chiều") is False


def test_is_short_time_answer_false_without_any_date_or_time():
    assert is_short_time_answer("chào em") is False


def test_mentions_time_or_number_true_for_weekday():
    assert mentions_time_or_number("thứ năm được không") is True


def test_mentions_time_or_number_false_for_plain_chat():
    assert mentions_time_or_number("chào em") is False


# ---------------------------------------------------------------------------
# AST: module không được chứa hằng chuỗi "trông giống" từ khoá ý định
# ---------------------------------------------------------------------------

# Hằng chuỗi hợp lệ DUY NHẤT: tên route/rule trong chữ ký kiểu Literal.
_ALLOWED_STRING_LITERALS = {
    "button",
    "classify_only",
    "orchestrate",
    "R0",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
}


def test_resolver_module_has_no_string_literals_that_look_like_intent_words():
    """Resolver không được so khớp theo từ ("ừ", "không", "đồng ý" ...) — mọi
    quyết định dựa trên cấu trúc `ctx` và các scanner của `tokens`. Hằng chuỗi
    duy nhất được phép là tên route/rule; docstring không bị tính."""
    source = inspect.getsource(resolver_module)
    tree = ast.parse(source)

    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_ids.add(id(body[0].value))

    offending = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            if node.value in _ALLOWED_STRING_LITERALS:
                continue
            offending.append(node.value)

    assert offending == [], f"Hằng chuỗi lạ trong resolver.py (nghi từ khoá ý định): {offending}"
