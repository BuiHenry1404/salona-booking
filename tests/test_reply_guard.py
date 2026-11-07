"""Task 4.3: `guard.py` — guard sự thật (§9.8 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

`rewrite_once` giả qua `monkeypatch.setattr("app.agents.booking.rewrite.rewrite_once",
...)` — guard.py gọi nó qua module (`rewrite.rewrite_once`), không qua một
binding `from ... import rewrite_once`, nên patch module mới có tác dụng.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from app.agents.booking.guard import (
    apply_guard,
    check_reply,
    facts_from_brief,
    normalize,
)
from app.agents.booking.schemas import Fact, ReplyBrief

REWRITE_ONCE = "app.agents.booking.rewrite.rewrite_once"


def make_brief(**overrides) -> ReplyBrief:
    defaults = dict(
        kind="answer",
        address="chị Lan",
        facts=[],
        ask=None,
        must_say=[],
        must_not_claim=[],
        then=None,
        previous_reply=None,
        tone_context="first_time",
        digest_text="",
        staff_note=None,
        now_local=datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc),
        fallback="Dạ chị Lan, em chưa ghi được, chị nhắn lại giúp em nhé.",
    )
    defaults.update(overrides)
    return ReplyBrief(**defaults)


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------


def test_normalize_casefolds_and_collapses_spacing():
    assert normalize("  Ba   GIỜ  Chiều Mai  ") == "ba giờ chiều mai"


# ---------------------------------------------------------------------------
# facts_from_brief()
# ---------------------------------------------------------------------------


def test_facts_from_brief_collects_clocks_from_iso():
    brief = make_brief(
        facts=[
            Fact(key="held_at", spoken="ba giờ chiều mai", iso="2026-11-07T15:00:00+07:00"),
            Fact(key="slot", spoken="bốn giờ chiều mai", iso="2026-11-07T16:00:00+07:00"),
        ],
        must_not_claim=["booked"],
    )

    facts = facts_from_brief(brief)

    assert (3, 0, "chiều") in facts["clocks"]
    assert (4, 0, "chiều") in facts["clocks"]
    assert facts["held_iso"] == "2026-11-07T15:00:00+07:00"
    assert facts["allowed_claims"] == {"cancelled", "moved"}


def test_facts_from_brief_held_iso_is_none_without_a_held_fact_key():
    brief = make_brief(facts=[Fact(key="slot", spoken="ba giờ chiều mai", iso="2026-11-07T15:00:00+07:00")])

    assert facts_from_brief(brief)["held_iso"] is None


# ---------------------------------------------------------------------------
# check_reply()
# ---------------------------------------------------------------------------


def test_missing_must_say_is_a_violation():
    brief = make_brief(must_say=["ba giờ chiều mai"])

    assert check_reply("Dạ chị ơi, em chào chị.", brief, []) == ["missing_fact"]


def test_must_say_match_ignores_case_and_spacing():
    brief = make_brief(must_say=["ba giờ chiều mai"])
    draft = "Dạ   chị, em giữ lịch BA   GIỜ   CHIỀU  MAI  cho chị nhé."

    assert "missing_fact" not in check_reply(draft, brief, [])


def test_claim_booked_when_forbidden_is_a_violation():
    brief = make_brief(must_not_claim=["booked"])

    assert "claim" in check_reply("Dạ chị ơi, em đã đặt lịch cho chị rồi ạ.", brief, [])


def test_claim_is_allowed_when_the_clock_matches_a_known_fact():
    brief = make_brief(
        facts=[Fact(key="booked_at", spoken="ba giờ chiều mai", iso="2026-11-07T15:00:00+07:00")],
        must_not_claim=[],
    )

    assert "claim" not in check_reply("Dạ chị ơi, em đã đặt lịch ba giờ chiều mai cho chị rồi ạ.", brief, [])


def test_claim_cancelled_when_forbidden_is_a_violation():
    """Fix round 1: huỷ/dời không có trong từ vựng nhận biết của `has_false_claim`
    cũ (chỉ bắt 'đặt') — cần đường kiểm riêng cho hai động từ này."""
    brief = make_brief(kind="failed", must_not_claim=["booked", "cancelled", "moved"])
    draft = "Dạ em đã huỷ lịch 3 giờ chiều mai cho chị rồi ạ."

    assert "claim" in check_reply(draft, brief, [])


def test_claim_moved_when_forbidden_is_a_violation():
    brief = make_brief(kind="limit_reached", must_not_claim=["booked", "cancelled", "moved"])
    draft = "Dạ em dời sang 4 giờ rồi nhé."

    assert "claim" in check_reply(draft, brief, [])


def test_claim_cancelled_is_allowed_when_not_forbidden():
    brief = make_brief(kind="confirmed", must_not_claim=["booked", "moved"])
    draft = "Dạ em đã huỷ lịch 3 giờ chiều mai cho chị rồi ạ."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_verb_without_a_completion_marker_is_not_a_violation():
    brief = make_brief(must_not_claim=["moved"])
    draft = "Dạ chị muốn dời sang giờ khác không ạ?"

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_changing_your_mind_is_not_a_booking_claim():
    """Fix round 2: "đổi" cũng dùng cho "đổi ý" — không phải lịch hẹn nào cả."""
    brief = make_brief(must_not_claim=["moved"])
    draft = "Dạ em đã đổi ý rồi ạ, chị thông cảm nhé."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_changing_a_phone_number_is_not_an_appointment_claim():
    brief = make_brief(must_not_claim=["moved"])
    draft = "Dạ em đã đổi số điện thoại rồi, chị lưu lại giúp em nhé."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_a_question_about_moving_is_not_a_claim():
    """Fix round 3: câu hỏi (kết thúc '?') không phải lời nhận đã làm."""
    brief = make_brief(must_not_claim=["moved"])
    draft = "Chị muốn dời lịch sang 4 giờ không ạ?"

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_negated_with_chua_is_not_a_claim():
    brief = make_brief(must_not_claim=["cancelled"])
    draft = "Dạ em chưa huỷ được lịch 3 giờ chiều mai, chị chờ em xíu nhé."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_negated_with_khong_is_not_a_claim():
    brief = make_brief(must_not_claim=["moved"])
    draft = "Dạ em không dời lịch đâu ạ, lịch vẫn 3 giờ."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_negated_with_chua_right_next_to_a_marker_is_not_a_claim():
    """Ca thật kiểm cơ chế phủ định: có cả 'chưa' VÀ 'rồi' quanh động từ —
    round 2 (chưa có phủ định) sẽ bắt oan câu này."""
    brief = make_brief(must_not_claim=["cancelled"])
    draft = "Dạ em chưa huỷ lịch rồi ạ, chị đừng lo."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_negated_with_khong_right_next_to_a_marker_is_not_a_claim():
    brief = make_brief(must_not_claim=["moved"])
    draft = "Dạ em không dời lịch rồi nhé."

    assert "claim" not in check_reply(draft, brief, [])


def test_claim_cancelled_and_moved_still_flag_after_questions_and_negations_are_skipped():
    """Round 3 không được làm mất lại hai ca bắt buộc của round 1."""
    cancel_brief = make_brief(must_not_claim=["cancelled"])
    assert "claim" in check_reply(
        "Dạ em đã huỷ lịch 3 giờ chiều mai cho chị rồi ạ.", cancel_brief, []
    )

    move_brief = make_brief(must_not_claim=["moved"])
    assert "claim" in check_reply("Dạ em dời sang 4 giờ rồi nhé.", move_brief, [])


async def test_apply_guard_fixes_a_forbidden_cancel_claim_via_unclaim(monkeypatch):
    """`unclaim` (tất định, đã có sẵn) đủ sức sửa câu "đã huỷ ... rồi" —
    không cần tới rewrite (LLM)."""
    mock = AsyncMock(side_effect=AssertionError("rewrite_once should not run"))
    monkeypatch.setattr(REWRITE_ONCE, mock)
    brief = make_brief(kind="failed", must_not_claim=["cancelled"])
    draft = "Dạ em đã huỷ lịch ba giờ chiều mai cho chị rồi ạ."

    result = await apply_guard(draft, brief, [], source="llm")

    assert result.violations == []
    assert result.source == "llm"
    mock.assert_not_awaited()


async def test_failed_cancel_claim_falls_back_when_unclaim_and_rewrite_cannot_fix_it(monkeypatch):
    unfixed = "Chị ơi lịch đã huỷ rồi nha."
    monkeypatch.setattr(REWRITE_ONCE, AsyncMock(return_value=unfixed))
    brief = make_brief(kind="failed", must_not_claim=["cancelled"])

    result = await apply_guard(unfixed, brief, [], source="llm")

    assert result.source == "code"
    assert result.answer == brief.fallback
    assert "claim" in result.violations


# ---------------------------------------------------------------------------
# normalize() — NFC/NFD (fix round 1, item 3)
# ---------------------------------------------------------------------------


def test_normalize_matches_nfc_and_nfd_forms_of_the_same_text():
    import unicodedata

    nfc = unicodedata.normalize("NFC", "ba giờ chiều mai")
    nfd = unicodedata.normalize("NFD", "ba giờ chiều mai")
    assert nfc != nfd  # mã byte khác nhau, cùng hiển thị

    assert normalize(nfc) == normalize(nfd)


# ---------------------------------------------------------------------------
# apply_guard()
# ---------------------------------------------------------------------------


async def test_tool_name_in_draft_is_stripped_before_checks():
    brief = make_brief(must_say=["ba giờ chiều mai"])
    draft = "Dạ chị, em giữ lịch ba giờ chiều mai cho chị nhé (propose_appointment)."

    result = await apply_guard(draft, brief, [], source="llm")

    assert "propose_appointment" not in result.answer
    assert result.violations == []
    assert result.source == "llm"


async def test_confirmed_with_wrong_time_falls_back_to_code_phrase(monkeypatch):
    monkeypatch.setattr(REWRITE_ONCE, AsyncMock(return_value=None))
    fallback = "Dạ chị Lan, em đặt xong lịch ba giờ chiều mai cho chị rồi nhé."
    brief = make_brief(kind="confirmed", must_say=["ba giờ chiều mai"], fallback=fallback)
    draft = "Dạ chị ơi, em giữ lịch bốn giờ chiều mai cho chị rồi nhé."

    result = await apply_guard(draft, brief, [], source="llm")

    assert result.source == "code"
    assert result.answer == fallback
    assert "missing_fact" in result.violations


async def test_rewrite_runs_once_and_fixes_missing_fact(monkeypatch):
    fixed = "Dạ chị ơi, em giữ lịch ba giờ chiều mai cho chị nhé."
    mock = AsyncMock(return_value=fixed)
    monkeypatch.setattr(REWRITE_ONCE, mock)
    brief = make_brief(must_say=["ba giờ chiều mai"])
    draft = "Dạ chị ơi, em chào chị."

    result = await apply_guard(draft, brief, [], source="llm")

    assert result.answer == fixed
    assert result.source == "llm"
    assert result.violations == []
    mock.assert_awaited_once()


async def test_only_repeat_keeps_the_rewrite(monkeypatch):
    prev_reply = "Dạ chị ơi, tiệm mở cửa tám giờ sáng tới bảy giờ tối nhé."
    mock = AsyncMock(return_value=prev_reply)
    monkeypatch.setattr(REWRITE_ONCE, mock)
    brief = make_brief()

    result = await apply_guard(prev_reply, brief, [prev_reply], source="llm")

    assert result.answer == prev_reply
    assert result.source == "llm"
    assert result.violations == ["repeat"]
    mock.assert_awaited_once()


async def test_answer_kind_with_unfixable_violation_keeps_the_draft_and_logs(monkeypatch):
    monkeypatch.setattr(REWRITE_ONCE, AsyncMock(return_value="Dạ chị ơi, em xin lỗi chị."))
    brief = make_brief(kind="answer", must_say=["ba giờ chiều mai"])
    draft = "Dạ chị ơi, em chào chị."

    with structlog.testing.capture_logs() as logs:
        result = await apply_guard(draft, brief, [], source="llm")

    assert result.answer == draft
    assert result.source == "llm"
    assert "missing_fact" in result.violations
    assert [e for e in logs if e["event"] == "guard_violation" and e["extra"]["fixed_by"] == "none"]


async def test_code_source_is_passed_through_untouched():
    brief = make_brief()
    draft = "Dạ chị ơi, lịch chưa ghi được."

    result = await apply_guard(draft, brief, [], source="code")

    assert result.answer == draft
    assert result.source == "code"
    assert result.violations == []


# ---------------------------------------------------------------------------
# Fix round 2–3 (Task 5.3): CHỈ dòng R0 (`announced_line`, code viết) được miễn
# kiểm claim; mọi câu `must_say` khác (giờ trần) vẫn nằm trong câu được kiểm.
# ---------------------------------------------------------------------------

_R0_ISO = "2026-11-07T15:00:00+07:00"
_R0_BOOKED = "đã đặt xong 3 giờ chiều mai"
_R0_MOVED = "đã dời xong 3 giờ chiều mai"
_PRIOR = Fact(key="prior_outcome", text="approved", spoken="3 giờ chiều mai", iso=_R0_ISO)
_ALL = ["booked", "cancelled", "moved"]


def _r0_brief(kind: str, line: str, *, must_not_claim=(), extra_must_say=()) -> ReplyBrief:
    return make_brief(
        kind=kind, facts=[_PRIOR], must_say=[line, *extra_must_say],
        must_not_claim=list(must_not_claim), announced_line=line,
    )


_CANCEL_BRIEF = make_brief(
    kind="proposal_made",
    facts=[Fact(key="will_cancel", spoken="Thứ Bảy 7/11, 3 giờ chiều", iso=_R0_ISO)],
    must_say=["Thứ Bảy 7/11, 3 giờ chiều"],
    must_not_claim=["cancelled"],
)
_MOVE_BRIEF = make_brief(
    kind="proposal_made",
    facts=[
        Fact(key="held_at", spoken="3 giờ chiều mai", iso=_R0_ISO),
        Fact(key="replaces", spoken="9 giờ sáng mai", iso="2026-11-07T09:00:00+07:00"),
    ],
    must_say=["3 giờ chiều mai"],
    must_not_claim=["moved"],
)


@pytest.mark.parametrize(
    "brief, draft, expected_claim",
    [
        # Dòng R0 approved một mình trên lượt xã giao — câu code viết, không phải claim.
        (_r0_brief("social", _R0_BOOKED), f"Dạ chị Lan, em {_R0_BOOKED} rồi ạ.", False),
        # Lượt limit cấm MỌI claim, nhưng dòng R0 "đã dời xong" vẫn là sự thật code viết.
        (_r0_brief("limit_reached", _R0_MOVED, must_not_claim=_ALL),
         f"Dạ chị Lan, em {_R0_MOVED} rồi ạ.", False),
        # Cùng câu đó mà bịa thêm một lịch không có trong facts → vẫn bắt.
        (_r0_brief("limit_reached", _R0_MOVED, must_not_claim=_ALL),
         f"Dạ chị Lan, em {_R0_MOVED} rồi ạ. Em đã đặt 5 giờ chiều cho chị rồi.", True),
        # Ké giờ khác vào CÂU của dòng R0 → claim theo động từ R0.
        (_r0_brief("social", _R0_BOOKED),
         f"Dạ em {_R0_BOOKED} và 5 giờ chiều ngày kia cho chị rồi.", True),
        # Giờ trần trong must_say (không phải dòng R0) KHÔNG được miễn kiểm.
        (_CANCEL_BRIEF, "Dạ em đã huỷ Thứ Bảy 7/11, 3 giờ chiều cho chị rồi ạ.", True),
        (_MOVE_BRIEF, "Dạ em đã dời sang 3 giờ chiều mai cho chị rồi ạ.", True),
    ],
)
def test_only_the_announced_line_is_exempt_from_claim_checks(brief, draft, expected_claim):
    codes = check_reply(draft, brief, [])

    assert ("claim" in codes) is expected_claim
    assert "missing_fact" not in codes


async def test_limit_reached_still_missing_the_phone_after_rewrite_falls_back_to_it(monkeypatch):
    phone_line = "Chị Lan gọi giúp em số 0900000000 nhé."
    monkeypatch.setattr(REWRITE_ONCE, AsyncMock(return_value="Dạ em xin lỗi chị ạ."))
    brief = make_brief(
        kind="limit_reached", must_say=[phone_line], must_not_claim=_ALL, fallback=phone_line,
    )

    result = await apply_guard("Dạ em xin lỗi chị nhé.", brief, [], source="llm")

    assert result.answer == phone_line
    assert result.source == "code"
