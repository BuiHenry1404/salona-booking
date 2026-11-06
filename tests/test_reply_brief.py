"""Task 4.1: `reply_brief.py` — bảng ánh xạ §5.4.

`facts_for`/`reply_kind_for`/`build_reply_brief` là hàm thuần (không Mongo,
không LLM) — mọi test ở đây dựng `CallRecord`/`PendingOutcome`/`TaskResult`
giả và `TurnContext` qua `tests.factories.make_ctx`.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.agents.booking.context import PendingProposal, UnannouncedOutcome
from app.agents.booking.orchestrator.agent import OrchestrateOutcome
from app.agents.booking.reply_brief import (
    FAILED_FALLBACK,
    PROPOSAL_FALLBACK,
    BUTTON_STALE_REPLY,
    ButtonOutcome,
    build_reply_brief,
    facts_for,
    reply_kind_for,
)
from app.agents.booking.schemas import (
    AppointmentList,
    AppointmentRefOut,
    AskCustomerArgs,
    CallRecord,
    FinishArgs,
    FreeSlots,
    Moment,
    PendingOutcome,
    ProposalCreated,
    ResolvedTime,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
)
from app.agents.booking_graph.middleware.tools import FALLBACK_ANSWER
from app.agents.booking_graph.phrase import code_phrase
from app.core.clock import now_utc
from app.core.config import settings
from tests.factories import make_ctx


def _m(spoken: str, iso: str = "2026-11-10T15:00:00+07:00") -> Moment:
    return Moment(iso=iso, spoken=spoken)


def _task_result(action: str, *, status="ok", data=None, error_code=None, asked=None, nearest=None) -> TaskResult:
    return TaskResult(
        task_id="t1", agent="appointment" if action != "get_hours" else "shop_info",
        action=action, status=status, data=data, error_code=error_code,
        asked=asked, nearest=nearest or [],
    )


def _call(tool: str, action: str | None, result, *, allowed: bool = True) -> CallRecord:
    return CallRecord(
        round=1, index=0, tool=tool, action=action, args_norm="{}",
        allowed=allowed, result=result, at=now_utc(),
    )


def _pending(outcome: str, **overrides) -> PendingOutcome:
    defaults = dict(outcome=outcome, proposal_id="p1", kind="book", spoken="3 giờ chiều mai")
    defaults.update(overrides)
    return PendingOutcome(**defaults)


def _outcome(**overrides) -> OrchestrateOutcome:
    defaults = dict(
        calls=[], pending_outcome=None, ended_by="finish",
        finish=FinishArgs(kind_hint="answer", service=None, stated=[]),
        ask=None, proposals_created=0, rounds_executed=1,
    )
    defaults.update(overrides)
    return OrchestrateOutcome(**defaults)


CTX = make_ctx()


# ---------------------------------------------------------------------------
# facts_for — một test, một dòng bảng §5.4 mỗi case (parametrize)
# ---------------------------------------------------------------------------


def _case_book():
    data = ProposalCreated(proposal_id="p1", kind="book", start_at=_m("3 giờ chiều mai"), note="da lieu", replaces=None, queued=[])
    return _call("appointment_task", "propose_book", _task_result("propose_book", data=data)), \
        {"held_at", "note"}, ["3 giờ chiều mai"], ["booked"]


def _case_move():
    data = ProposalCreated(proposal_id="p1", kind="move", start_at=_m("4 giờ chiều mai"), note=None,
                            replaces=_m("2 giờ chiều hôm qua", "2026-11-05T14:00:00+07:00"), queued=[])
    return _call("appointment_task", "propose_move", _task_result("propose_move", data=data)), \
        {"held_at", "replaces"}, ["4 giờ chiều mai"], ["moved"]


def _case_cancel():
    data = ProposalCreated(proposal_id="p1", kind="cancel", start_at=_m("5 giờ chiều mai"), note=None, replaces=None, queued=[])
    return _call("appointment_task", "propose_cancel", _task_result("propose_cancel", data=data)), \
        {"will_cancel"}, ["5 giờ chiều mai"], ["cancelled"]


def _case_queued():
    data = ProposalCreated(proposal_id="p1", kind="book", start_at=_m("3 giờ chiều mai"), note=None, replaces=None,
                            queued=[_m("6 giờ chiều mai", "2026-11-10T18:00:00+07:00")])
    return _call("appointment_task", "propose_book", _task_result("propose_book", data=data)), \
        {"held_at", "queued_at"}, ["3 giờ chiều mai"], ["booked"]


def _case_free_slots():
    data = FreeSlots(day=CTX.today, slots=[_m("3 giờ chiều mai"), _m("4 giờ chiều mai", "2026-11-10T16:00:00+07:00")], more=True, fetched_at=now_utc())
    return _call("appointment_task", "find_slots", _task_result("find_slots", data=data)), \
        {"slot", "day", "more"}, ["3 giờ chiều mai", "4 giờ chiều mai"], []


def _case_slot_unavailable():
    result = _task_result("propose_book", status="failed", error_code="slot_unavailable",
                           asked=_m("3 giờ chiều mai"), nearest=[_m("4 giờ chiều mai", "2026-11-10T16:00:00+07:00")])
    return _call("appointment_task", "propose_book", result), {"asked", "nearest"}, ["3 giờ chiều mai"], []


def _case_outside_hours():
    result = _task_result("propose_book", status="failed", error_code="outside_hours", asked=_m("9 giờ tối mai", "2026-11-10T21:00:00+07:00"))
    return _call("appointment_task", "propose_book", result), {"reason", "asked", "open", "close"}, ["9 giờ tối mai"], []


def _case_already_yours():
    result = _task_result("propose_book", status="failed", error_code="already_yours", asked=_m("3 giờ chiều mai"))
    return _call("appointment_task", "propose_book", result), {"existing"}, ["3 giờ chiều mai"], []


def _case_resolved_time_missing():
    data = ResolvedTime(start_at=None, partial_date=None, missing=["giờ cụ thể"], day_from=None)
    return _call("appointment_task", "resolve_time", _task_result("resolve_time", data=data)), set(), [], []


def _case_appointment_list():
    data = AppointmentList(appointments=[AppointmentRefOut(id="a1", at=_m("3 giờ chiều mai"), note=None)])
    return _call("appointment_task", "list_appointments", _task_result("list_appointments", data=data)), \
        {"appointment"}, ["3 giờ chiều mai"], []


def _case_shop_hours():
    data = ShopHoursOut(open=_m("8 giờ sáng", "2026-11-10T08:00:00+07:00"), close=_m("7 giờ tối", "2026-11-10T19:00:00+07:00"), closed_days=[])
    return _call("shop_info_task", "get_hours", _task_result("get_hours", data=data)), \
        {"open", "close"}, ["8 giờ sáng", "7 giờ tối"], []


def _case_shop_status():
    data = ShopStatusOut(busy=True, until=_m("4 giờ chiều", "2026-11-10T16:00:00+07:00"))
    return _call("shop_info_task", "get_status", _task_result("get_status", data=data)), \
        {"busy", "until"}, ["4 giờ chiều"], []


def _case_shop_open():
    data = ShopOpenOut(at=_m("3 giờ chiều mai"), open=True, reason="open", next_change=None)
    return _call("shop_info_task", "is_open_at", _task_result("is_open_at", data=data)), \
        {"at", "open", "reason"}, ["3 giờ chiều mai"], []


def _case_pending_approved():
    pending = _pending("approved", kind="book", spoken="3 giờ chiều mai",
                        appointment=AppointmentRefOut(id="a1", at=_m("3 giờ chiều mai")))
    return _call("decide_pending", None, pending), {"booked_at"}, ["3 giờ chiều mai"], []


def _case_pending_rejected():
    pending = _pending("rejected", kind="book", spoken="3 giờ chiều mai")
    return _call("decide_pending", None, pending), {"declined"}, [], ["booked"]


def _case_pending_clarify():
    pending = _pending("clarify", kind="move", spoken="4 giờ chiều mai")
    return _call("decide_pending", None, pending), {"pending"}, ["4 giờ chiều mai"], ["moved"]


def _case_pending_kept():
    pending = _pending("kept", kind="cancel", spoken="5 giờ chiều mai")
    return _call("decide_pending", None, pending), {"pending"}, [], ["cancelled"]


def _case_pending_failed():
    pending = _pending("failed", kind="book", spoken="3 giờ chiều mai", error="SlotTaken",
                        nearest=[_m("4 giờ chiều mai", "2026-11-10T16:00:00+07:00")])
    return _call("decide_pending", None, pending), {"asked", "error_reason", "nearest"}, \
        ["3 giờ chiều mai"], ["booked", "cancelled", "moved"]


def _case_pending_not_approvable_expired():
    pending = _pending("not_approvable", kind="book", spoken="3 giờ chiều mai", reason="expired")
    return _call("decide_pending", None, pending), {"expired"}, [], ["booked", "cancelled", "moved"]


def _case_pending_not_approvable_not_found():
    pending = _pending("not_approvable", kind="book", spoken="3 giờ chiều mai", reason="not_found")
    return _call("decide_pending", None, pending), {"error_reason"}, [], ["booked", "cancelled", "moved"]


def _case_pending_kept_with_expiry():
    expires_at = now_utc() + timedelta(minutes=7)
    pending = _pending("kept", kind="cancel", spoken="5 giờ chiều mai", expires_at=expires_at)
    return _call("decide_pending", None, pending), {"pending", "expires_in"}, [], ["cancelled"]


@pytest.mark.parametrize(
    "build_case",
    [
        _case_book, _case_move, _case_cancel, _case_queued, _case_free_slots,
        _case_slot_unavailable, _case_outside_hours, _case_already_yours,
        _case_resolved_time_missing, _case_appointment_list, _case_shop_hours,
        _case_shop_status, _case_shop_open, _case_pending_approved,
        _case_pending_rejected, _case_pending_clarify, _case_pending_kept,
        _case_pending_kept_with_expiry, _case_pending_failed,
        _case_pending_not_approvable_expired, _case_pending_not_approvable_not_found,
    ],
    ids=lambda f: f.__name__,
)
def test_facts_for_table(build_case):
    record, expected_keys, expected_must_say, expected_must_not_claim = build_case()
    facts, must_say, must_not_claim = facts_for(record, CTX, now_utc())
    assert {f.key for f in facts} == expected_keys
    assert must_say == expected_must_say
    assert must_not_claim == expected_must_not_claim


def test_kept_expiry_fact_carries_iso_and_minutes_left():
    expires_at = now_utc() + timedelta(minutes=7)
    pending = _pending("kept", kind="cancel", spoken="5 giờ chiều mai", expires_at=expires_at)
    record = _call("decide_pending", None, pending)
    now = now_utc()
    facts, _say, _claim = facts_for(record, CTX, now)
    expiry = next(f for f in facts if f.key == "expires_in")
    assert expiry.iso == expires_at.isoformat()
    assert expiry.text in ("6", "7")  # làm tròn phút còn lại, không lệ thuộc mili-giây lúc chạy test


def test_outside_hours_includes_shop_open_and_close_from_ctx():
    ctx = make_ctx()
    result = _task_result("propose_book", status="failed", error_code="outside_hours", asked=_m("9 giờ tối mai", "2026-11-10T21:00:00+07:00"))
    record = _call("appointment_task", "propose_book", result)
    facts, _say, _claim = facts_for(record, ctx, now_utc())
    keys = {f.key for f in facts}
    assert {"reason", "asked", "open", "close"} <= keys
    opened = next(f for f in facts if f.key == "open")
    closed = next(f for f in facts if f.key == "close")
    assert opened.text == ctx.shop_hours.open_time
    assert closed.text == ctx.shop_hours.close_time


def test_rejected_calls_never_contribute_facts():
    pending = _pending("approved")
    outcome = _outcome(calls=[_call("decide_pending", None, pending, allowed=False)])
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.facts == []
    assert brief.kind != "confirmed"


# ---------------------------------------------------------------------------
# kind — thứ tự ưu tiên
# ---------------------------------------------------------------------------


def test_kind_precedence_confirmed_beats_everything():
    pending = _pending("approved")
    proposal = ProposalCreated(proposal_id="p2", kind="book", start_at=_m("8 giờ tối mai", "2026-11-10T20:00:00+07:00"), note=None, replaces=None, queued=[])
    calls = [_call("appointment_task", "propose_book", _task_result("propose_book", data=proposal))]
    outcome = _outcome(
        calls=calls, pending_outcome=pending, ended_by="limit", finish=None,
        ask=AskCustomerArgs(field="day", hint=None), proposals_created=1, rounds_executed=99,
    )
    assert reply_kind_for(outcome, CTX) == "confirmed"


def test_declined_without_new_proposal_vs_proposal_made_after_change():
    pending = _pending("superseded")

    no_new_proposal = _outcome(pending_outcome=pending)
    assert reply_kind_for(no_new_proposal, CTX) == "declined"

    proposal = ProposalCreated(proposal_id="p2", kind="book", start_at=_m("8 giờ tối mai", "2026-11-10T20:00:00+07:00"), note=None, replaces=None, queued=[])
    calls = [_call("appointment_task", "propose_book", _task_result("propose_book", data=proposal))]
    with_new_proposal = _outcome(calls=calls, pending_outcome=pending, proposals_created=1, finish=None)
    assert reply_kind_for(with_new_proposal, CTX) == "proposal_made"


def test_brief_not_approvable_has_expired_fact_and_no_claims():
    pending = _pending("not_approvable", reason="expired")
    outcome = _outcome(calls=[_call("decide_pending", None, pending)], pending_outcome=pending, finish=None)
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "failed"
    assert any(f.key == "expired" for f in brief.facts)
    assert brief.must_not_claim == ["booked", "cancelled", "moved"]


def test_button_outcome_stale_maps_to_stale_button():
    outcome = ButtonOutcome(kind="stale", outcome=None)
    assert reply_kind_for(outcome, CTX) == "stale_button"


def test_button_outcome_approved_maps_to_confirmed():
    outcome = ButtonOutcome(kind="approved", outcome=_pending("approved"))
    assert reply_kind_for(outcome, CTX) == "confirmed"


def test_orchestrator_finish_social_maps_directly_to_social_kind():
    """Task 4.4b: không còn `classify_only`/`ClassifyOutcome` — `social` đến
    thẳng từ `finish(kind_hint="social")` của Orchestrator."""
    outcome = _outcome(finish=FinishArgs(kind_hint="social", service=None, stated=[]))
    assert reply_kind_for(outcome, CTX) == "social"


# ---------------------------------------------------------------------------
# unannounced / recently_declined / unsupported
# ---------------------------------------------------------------------------


def test_unannounced_outcome_leads_must_say():
    ctx = make_ctx(unannounced=UnannouncedOutcome(
        proposal_id="p0", outcome="approved", kind="book", spoken="2 giờ chiều mai",
        iso="2026-11-07T14:00:00+07:00",
    ))
    outcome = _outcome()
    brief = build_reply_brief(ctx, outcome, previous_reply=None, now=now_utc())
    assert brief.must_say[0] == "đã đặt xong 2 giờ chiều mai"
    # Fix round 2 Task 5.3: guard biết mốc giờ của dòng R0 (đọc từ `iso`).
    prior = [f for f in brief.facts if f.key == "prior_outcome"]
    assert prior and prior[0].iso == "2026-11-07T14:00:00+07:00"


def test_unannounced_failed_outcome_uses_the_failed_line():
    ctx = make_ctx(unannounced=UnannouncedOutcome(
        proposal_id="p0", outcome="failed", kind="book", spoken="2 giờ chiều mai", error="SlotTaken",
    ))
    outcome = _outcome()
    brief = build_reply_brief(ctx, outcome, previous_reply=None, now=now_utc())
    assert brief.must_say[0] == "2 giờ chiều mai em chưa ghi được"


def test_recently_declined_blocks_claim_when_appointment_task_called():
    declined = PendingProposal(
        id="p9", kind="cancel", start_at=CTX.now_local, spoken="3 giờ chiều mai",
        note=None, expires_at=CTX.now_local, unclear_count=0, target=None,
    )
    ctx = make_ctx(recently_declined=declined)
    data = FreeSlots(day=ctx.today, slots=[_m("5 giờ chiều mai", "2026-11-10T17:00:00+07:00")], more=False, fetched_at=now_utc())
    calls = [_call("appointment_task", "find_slots", _task_result("find_slots", data=data))]
    outcome = _outcome(calls=calls)
    brief = build_reply_brief(ctx, outcome, previous_reply=None, now=now_utc())
    assert any(f.key == "declined" for f in brief.facts)
    assert "cancelled" in brief.must_not_claim


def test_unsupported_carries_the_shop_phone():
    outcome = _outcome(finish=FinishArgs(kind_hint="unsupported", service=None, stated=[]))
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "unsupported"
    assert any(f.key == "shop_phone" and f.text == settings.shop_phone for f in brief.facts)


# ---------------------------------------------------------------------------
# fallback
# ---------------------------------------------------------------------------


def test_fallback_for_confirmed_comes_from_code_phrase_with_the_right_time():
    pending = _pending("approved", kind="book", spoken="3 giờ chiều mai")
    outcome = _outcome(calls=[_call("decide_pending", None, pending)], pending_outcome=pending, finish=None)
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "confirmed"
    expected = code_phrase(
        {"kind": "booked", "address": CTX.customer.address_phrase, "when": "3 giờ chiều mai", "context": brief.tone_context},
        [],
    )
    assert brief.fallback == expected
    assert "3 giờ chiều mai" in brief.fallback


def test_fallback_for_failed_and_proposal_made_use_the_pinned_copy():
    pending = _pending("failed", kind="book", spoken="3 giờ chiều mai", error="SlotTaken")
    failed_outcome = _outcome(calls=[_call("decide_pending", None, pending)], pending_outcome=pending, finish=None)
    failed_brief = build_reply_brief(CTX, failed_outcome, previous_reply=None, now=now_utc())
    assert failed_brief.fallback == FAILED_FALLBACK.format(address=CTX.customer.address_phrase, when="3 giờ chiều mai")

    proposal = ProposalCreated(proposal_id="p2", kind="book", start_at=_m("4 giờ chiều mai", "2026-11-10T16:00:00+07:00"), note=None, replaces=None, queued=[])
    calls = [_call("appointment_task", "propose_book", _task_result("propose_book", data=proposal))]
    proposal_outcome = _outcome(calls=calls, proposals_created=1, finish=None)
    proposal_brief = build_reply_brief(CTX, proposal_outcome, previous_reply=None, now=now_utc())
    assert proposal_brief.fallback == PROPOSAL_FALLBACK.format(address=CTX.customer.address_phrase, when="4 giờ chiều mai")


def test_stale_button_fallback_uses_the_address():
    outcome = ButtonOutcome(kind="stale", outcome=None)
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "stale_button"
    assert brief.fallback == BUTTON_STALE_REPLY.replace("anh chị", CTX.customer.address_phrase)


def test_limit_reached_still_lists_facts_gathered_so_far():
    data = FreeSlots(day=CTX.today, slots=[_m("3 giờ chiều mai")], more=False, fetched_at=now_utc())
    calls = [_call("appointment_task", "find_slots", _task_result("find_slots", data=data))]
    outcome = _outcome(
        calls=calls, ended_by="limit", finish=None, rounds_executed=settings.orch_max_tool_rounds,
    )
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "limit_reached"
    assert any(f.key == "slot" for f in brief.facts)
    assert any(f.key == "error_reason" and f.text == "limit" for f in brief.facts)
    assert brief.must_not_claim == ["booked", "cancelled", "moved"]
    assert brief.fallback == FALLBACK_ANSWER


def test_limit_reached_from_error_also_uses_the_hard_fallback():
    outcome = _outcome(ended_by="error", finish=None, rounds_executed=0)
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "limit_reached"
    assert any(f.key == "error_reason" and f.text == "error" for f in brief.facts)
    assert brief.must_not_claim == ["booked", "cancelled", "moved"]
    assert brief.fallback == FALLBACK_ANSWER


def test_limit_reached_keeps_confirmed_precedence_when_pending_approved():
    """Precedence stays per ruling 1: dù ended_by rơi vào limit, một
    `pending_outcome` đã `approved` vẫn phải thắng — `limit_reached` không
    được đè lên một lịch đã ghi thật."""
    pending = _pending("approved", kind="book", spoken="3 giờ chiều mai")
    outcome = _outcome(
        calls=[_call("decide_pending", None, pending)], pending_outcome=pending,
        ended_by="limit", finish=None, rounds_executed=settings.orch_max_tool_rounds,
    )
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "confirmed"
    assert brief.must_not_claim == []


def test_limit_reached_keeps_proposal_made_precedence_when_new_proposal_exists():
    proposal = ProposalCreated(proposal_id="p2", kind="book", start_at=_m("8 giờ tối mai", "2026-11-10T20:00:00+07:00"), note=None, replaces=None, queued=[])
    calls = [_call("appointment_task", "propose_book", _task_result("propose_book", data=proposal))]
    outcome = _outcome(
        calls=calls, ended_by="limit", finish=None, proposals_created=1,
        rounds_executed=settings.orch_max_tool_rounds,
    )
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.kind == "proposal_made"
    assert brief.must_not_claim == ["booked"]


def test_queued_adds_then_and_blocks_booked_claim_for_queued_time():
    proposal = ProposalCreated(
        proposal_id="p1", kind="book", start_at=_m("3 giờ chiều mai"), note=None, replaces=None,
        queued=[_m("5 giờ chiều mai", "2026-11-10T17:00:00+07:00")],
    )
    calls = [_call("appointment_task", "propose_book", _task_result("propose_book", data=proposal))]
    outcome = _outcome(calls=calls, proposals_created=1, finish=None)
    brief = build_reply_brief(CTX, outcome, previous_reply=None, now=now_utc())
    assert brief.then == "Sau giờ này em sẽ hỏi tiếp 5 giờ chiều mai"
    assert "booked" in brief.must_not_claim
