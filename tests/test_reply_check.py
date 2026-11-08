"""Task 2 (orchestrator-speaks): `reply_check.py` — trích sự thật của lượt,
kiểm câu trả lời Orchestrator viết, và câu mẫu code (§7 của
`docs/superpowers/specs/2026-11-08-orchestrator-speaks-design.md`).

Hàm thuần — không Mongo, không LLM. Mỗi dòng bảng dựng `TurnContext` bằng
`tests.factories.make_ctx` và `CallRecord`/`PendingOutcome` giả.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.agents.booking.context import PendingProposal, UnannouncedOutcome
from app.agents.booking.phrase import _TEMPLATES
from app.agents.booking.reply_check import (
    GENERIC_FALLBACK,
    build_reply_facts,
    check_reply_text,
    is_announced,
)
from app.agents.booking.schemas import (
    AppointmentRefOut,
    CallRecord,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    TaskResult,
)
from app.core.clock import now_utc
from app.core.config import settings
from tests.factories import make_ctx

PHONE = "0901 234 567"
FIFTEEN = "2026-11-09T15:00:00+07:00"
SIXTEEN = "2026-11-09T16:00:00+07:00"


@pytest.fixture(autouse=True)
def _shop_phone(monkeypatch):
    # `.env` của môi trường test có thể để trống SĐT — ghim một số giả.
    monkeypatch.setattr(settings, "shop_phone", PHONE)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _m(spoken: str, iso: str = FIFTEEN) -> Moment:
    return Moment(iso=iso, spoken=spoken)


def _call(tool: str, action: str | None, result, *, allowed: bool = True) -> CallRecord:
    return CallRecord(
        round=1, index=0, tool=tool, action=action, args_norm="{}",
        allowed=allowed, result=result, at=now_utc(),
    )


def _task(action: str, *, agent="appointment", status="ok", data=None, error_code=None,
          asked=None, nearest=None, missing=None) -> TaskResult:
    return TaskResult(
        task_id="t1.0", agent=agent, action=action, status=status, data=data,
        error_code=error_code, asked=asked, nearest=nearest or [], missing=missing or [],
    )


def _proposal_call(spoken: str = "3 giờ chiều mai", iso: str = FIFTEEN, *, allowed: bool = True) -> CallRecord:
    data = ProposalCreated(
        proposal_id="p1", kind="book", start_at=_m(spoken, iso), note=None, replaces=None, queued=[],
    )
    return _call("appointment_task", "propose_book", _task("propose_book", data=data), allowed=allowed)


def _needs_info_call(missing: str) -> CallRecord:
    data = ResolvedTime(start_at=None, partial_date=date(2026, 11, 9), missing=[missing], day_from=None)
    return _call("appointment_task", "resolve_time",
                 _task("resolve_time", status="needs_info", data=data, missing=[missing]))


def _pending_ctx(spoken: str = "3 giờ chiều mai", **overrides):
    pending = PendingProposal(
        id="p1", kind="book", start_at=datetime.fromisoformat(FIFTEEN), spoken=spoken,
        expires_at=now_utc() + timedelta(minutes=10), unclear_count=0,
    )
    return make_ctx(pending=pending, **overrides)


def _outcome(outcome: str, spoken: str = "3 giờ chiều mai", **overrides) -> PendingOutcome:
    defaults = dict(outcome=outcome, proposal_id="p1", kind="book", spoken=spoken)
    defaults.update(overrides)
    return PendingOutcome(**defaults)


def _approved() -> PendingOutcome:
    return _outcome(
        "approved", appointment=AppointmentRefOut(id="a1", at=_m("Thứ Hai 9/11, 3 giờ chiều")),
    )


def _decide_call(outcome: PendingOutcome, label: str = "approve") -> CallRecord:
    return _call("decide_pending", label, outcome)


def _hours_call() -> CallRecord:
    data = ShopHoursOut(
        open=Moment(iso="08:00", spoken="8 giờ sáng"),
        close=Moment(iso="19:00", spoken="7 giờ tối"),
        closed_days=[],
    )
    return _call("shop_info_task", "get_hours", _task("get_hours", agent="shop_info", data=data))


def _r0_ctx():
    return make_ctx(unannounced=UnannouncedOutcome(
        proposal_id="p0", outcome="approved", kind="book", spoken="3 giờ chiều mai",
        turn_id="t0", iso=FIFTEEN,
    ))


_APPROVED = _approved()
_KEPT = _outcome("kept")
_FAILED = _outcome("failed", error="slot_taken")
_SLOT_TAKEN = _call("appointment_task", "propose_book", _task(
    "propose_book", status="failed", error_code="slot_unavailable",
    asked=_m("3 giờ chiều mai"), nearest=[_m("4 giờ chiều mai", SIXTEEN)],
))

CHECK_TABLE = [
    pytest.param(
        _pending_ctx(), [], None, "ok em",
        "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ.", ["claim"],
        id="claim_before_approve",
    ),
    pytest.param(
        _pending_ctx(), [_decide_call(_APPROVED)], _APPROVED, "ừ được em",
        "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ.", [],
        id="claim_after_approve",
    ),
    pytest.param(
        _pending_ctx(), [_decide_call(_KEPT, "none"), _hours_call()], _KEPT,
        "tiệm mở cửa tới mấy giờ em?",
        "Dạ tiệm mở 8 giờ sáng tới 7 giờ tối ạ. Lịch 3 giờ chiều mai em vẫn đang giữ cho chị, "
        "chị đồng ý không ạ?", [],
        id="hold_wording_on_pending_slot",
    ),
    pytest.param(
        make_ctx(), [], None, "mai 3 giờ chiều được không em",
        "Dạ em đang giữ 3 giờ chiều mai cho chị ạ.", ["claim"],
        id="hold_wording_without_any_proposal",
    ),
    pytest.param(
        make_ctx(), [_proposal_call()], None, "3 giờ chiều nha em",
        "Em giữ chỗ 4 giờ chiều mai cho chị nhé, chị đồng ý không ạ?",
        ["claim", "unknown_time", "missing_fact"],
        id="invented_time",
    ),
    pytest.param(
        make_ctx(), [_needs_info_call("sáng hay chiều")], None,
        "mai chị ghé làm móng lúc 3 giờ nha em",
        "Dạ chị Lan, 3 giờ sáng hay 3 giờ chiều ạ?", [],
        id="echoes_customer_clock",
    ),
    pytest.param(
        make_ctx(), [_SLOT_TAKEN], None, "mai 3 giờ chiều nha",
        "Dạ 3 giờ có người đặt rồi, 4 giờ chiều còn trống ạ.", [],
        id="nearest_slot_clock",
    ),
    pytest.param(
        make_ctx(), [_proposal_call("Thứ Hai 9/11, 3 giờ chiều")], None, "3 giờ chiều nha em",
        "Em giữ chỗ Thứ Hai 9/11, 3 giờ chiều cho chị nhé.", ["missing_question"],
        id="proposal_without_question",
    ),
    pytest.param(
        make_ctx(), [_needs_info_call("giờ cụ thể")], None, "mai chị muốn làm móng",
        "Dạ chị cho em biết chị muốn đặt mấy giờ nhé ạ.", [],
        id="polite_ask_without_question_mark",
    ),
    pytest.param(
        _pending_ctx(), [_decide_call(_FAILED)], _FAILED, "ừ em",
        "Dạ lịch 3 giờ chiều mai em chưa ghi được ạ.", ["missing_phone"],
        id="failed_without_phone",
    ),
    pytest.param(
        make_ctx(), [_proposal_call()], None, "3 giờ chiều nha em",
        f"Dạ em giữ chỗ 3 giờ chiều mai cho chị, chị đồng ý không ạ? Cần gì chị gọi {PHONE} nhé.",
        ["phone_not_allowed"],
        id="phone_when_nothing_failed",
    ),
    pytest.param(
        _r0_ctx(), [], None, "chào em",
        "Dạ chào chị ạ.", ["missing_fact"],
        id="r0_missing",
    ),
]


@pytest.mark.parametrize("ctx, calls, outcome, question, text, codes", CHECK_TABLE)
def test_check_reply_table(ctx, calls, outcome, question, text, codes):
    facts = build_reply_facts(ctx, calls, outcome, question)

    assert check_reply_text(text, facts) == codes


FALLBACK_TABLE = [
    # (ctx, calls, outcome, có SĐT, phải có cụm, phải có "?")
    pytest.param(_pending_ctx(), [_decide_call(_FAILED)], _FAILED, True, "3 giờ chiều mai", False,
                 id="approve_failed"),
    pytest.param(_pending_ctx(), [_decide_call(_outcome("not_approvable", reason="expired"))],
                 _outcome("not_approvable", reason="expired"), True, "3 giờ chiều mai", False,
                 id="approve_not_approvable"),
    pytest.param(_pending_ctx(), [_decide_call(_APPROVED)], _APPROVED, False, "3 giờ chiều mai", False,
                 id="approved"),
    pytest.param(make_ctx(), [_proposal_call()], None, False, "3 giờ chiều mai", True,
                 id="new_proposal"),
    pytest.param(_pending_ctx(), [_hours_call()], None, False, "3 giờ chiều mai", True,
                 id="pending_kept"),
    pytest.param(make_ctx(), [_hours_call()], None, False, None, True,
                 id="nothing"),
]


@pytest.mark.parametrize("ctx, calls, outcome, has_phone, must_have, asks", FALLBACK_TABLE)
def test_fallback_has_the_phone_only_when_approve_failed(ctx, calls, outcome, has_phone, must_have, asks):
    facts = build_reply_facts(ctx, calls, outcome, "câu khách")

    assert (_digits(PHONE) in _digits(facts.fallback)) is has_phone
    assert ("?" in facts.fallback) is asks
    assert "gọi" not in facts.fallback or has_phone
    if must_have is None:
        assert facts.fallback == GENERIC_FALLBACK.format(address="chị Lan")
    else:
        assert must_have in facts.fallback
    # Câu mẫu tự nó phải qua được phép kiểm của lượt.
    assert check_reply_text(facts.fallback, facts) == []


@pytest.mark.parametrize("ctx, text, announced", [
    pytest.param(_r0_ctx(), "Dạ lịch 3 GIỜ   chiều mai của chị em ghi xong rồi ạ.", True, id="said"),
    pytest.param(_r0_ctx(), "Dạ chào chị ạ.", False, id="not_said"),
    pytest.param(make_ctx(), "Dạ lịch 3 giờ chiều mai ạ.", False, id="no_r0"),
])
def test_is_announced_detects_r0_spoken(ctx, text, announced):
    facts = build_reply_facts(ctx, [], None, "chào em")

    assert is_announced(text, facts) is announced
    if ctx.unannounced is not None:
        assert facts.announce_turn_id == "t0"
        assert facts.announce_spoken == "3 giờ chiều mai"


def test_build_reply_facts_ignores_rejected_calls():
    rejected = _proposal_call("4 giờ chiều mai", SIXTEEN, allowed=False)

    facts = build_reply_facts(make_ctx(), [rejected], None, "chào em")

    assert (4, 0, "chiều") not in facts.clocks
    assert facts.held_clocks == set()
    assert facts.must_mention == []
    assert facts.question_required is False
    assert "unknown_time" in check_reply_text("Dạ 4 giờ chiều mai còn trống ạ.", facts)


@pytest.mark.parametrize("kind, claim", [("book", "booked"), ("move", "moved"), ("cancel", "cancelled")])
def test_every_code_phrase_passes_the_check_after_approve(kind, claim):
    """Câu mẫu `approved` (`code_phrase`, mọi giọng) không bị chính phép kiểm
    của lượt đó bắt — không thì câu mẫu cuối cùng cũng là câu sai."""
    when = "Thứ Hai 9/11, 3 giờ chiều"
    outcome = _outcome("approved", spoken=when, kind=kind,
                       appointment=AppointmentRefOut(id="a1", at=_m(when)))
    facts = build_reply_facts(_pending_ctx(when), [_decide_call(outcome)], outcome, "ừ")

    for templates in _TEMPLATES[claim].values():
        for template in templates:
            text = template.format(address="chị Lan", when=when)
            assert check_reply_text(text, facts) == [], text
