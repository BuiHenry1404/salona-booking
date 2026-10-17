"""Test cho `bench/score.py` — dữ liệu dựng tay, không mạng, không Mongo thật."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from bench.adapters.base import TurnRecord
from bench.dbread import ApptRow
from bench.scenario import (
    ApptSpec,
    Expect,
    ExpectAppt,
    Scenario,
    Setup,
    Turn,
)
from bench.score import (
    RunScore,
    TEXT_RULES,
    aggregate,
    behavior_match,
    check_precondition,
    pass_hat_k,
    score_run,
)

VN_TZ = timezone(timedelta(hours=7))
NOW_VN = datetime(2026, 10, 17, 10, 0, tzinfo=VN_TZ)  # "hôm nay" = 2026-10-17
CUSTOMER_ID = "u-cust"


def _row(id_: str, user_id: str, dt: datetime, status: str = "booked", note=None) -> ApptRow:
    return ApptRow(id=id_, user_id=user_id, start_at=dt, status=status, note=note)


def _scenario(expect: Expect, turns: tuple[Turn, ...] = ()) -> Scenario:
    return Scenario(
        id="t",
        title="t",
        kind="scripted",
        tags=(),
        setup=Setup(),
        turns=turns,
        sim=None,
        expect=expect,
    )


def _turn_record(index: int, answer: str = "", tools=(), error=None, db_after=None) -> TurnRecord:
    return TurnRecord(
        index=index,
        customer_text="x",
        answer=answer,
        tools=list(tools),
        error=error,
        db_after=db_after,
    )


def test_ghost_booking_is_wrong_write():
    # G-01: expect booked=() (không ai được đặt gì), nhưng DB sau cùng có lịch 15:00
    # được tạo ra ngoài ý khách -> "extra:..." và trượt.
    scenario = _scenario(Expect(booked=()))
    before = []
    after = [_row("1", CUSTOMER_ID, datetime(2026, 10, 18, 15, 0, tzinfo=VN_TZ))]
    turns = [_turn_record(0, answer="Dạ em đã giữ chỗ giúp mình rồi ạ", db_after=after)]

    result = score_run(scenario, turns, before, after, CUSTOMER_ID, NOW_VN)

    assert len(result.wrong_writes) == 1
    assert result.wrong_writes[0].startswith("extra:")
    assert result.passed is False


def test_correct_booking_passes():
    expected_dt = datetime(2026, 10, 18, 9, 30, tzinfo=VN_TZ)
    scenario = _scenario(
        Expect(booked=(ExpectAppt(day="tomorrow", time="09:30"),))
    )
    before = []
    after = [_row("1", CUSTOMER_ID, expected_dt)]
    turns = [_turn_record(0, answer="Dạ em đã đặt xong ạ", db_after=after)]

    result = score_run(scenario, turns, before, after, CUSTOMER_ID, NOW_VN)

    assert result.wrong_writes == []
    assert result.passed is True
    assert result.valid is True


def test_other_customer_change_is_wrong_write():
    scenario = _scenario(Expect(booked=()))
    other_before = _row("2", "u-other", datetime(2026, 10, 18, 11, 0, tzinfo=VN_TZ))
    before = [other_before]
    after = [
        _row("2", "u-other", datetime(2026, 10, 18, 11, 0, tzinfo=VN_TZ), status="cancelled")
    ]
    turns = [_turn_record(0, answer="Dạ vâng ạ", db_after=after)]

    result = score_run(scenario, turns, before, after, CUSTOMER_ID, NOW_VN)

    assert result.wrong_writes == ["other_customer:2"]
    assert result.passed is False


def test_invalid_when_precondition_fails():
    # Lượt 1 (index 1) đòi hỏi lượt 0 phải nhắc 15:00 ngày mai, nhưng answer
    # lượt 0 không nhắc gì -> invalid.
    turn0 = Turn(say="Tôi muốn đặt lịch")
    turn1 = Turn(
        intent="agree",
        requires_asked=ApptSpec(day="tomorrow", time="15:00"),
    )
    scenario = _scenario(Expect(booked=None), turns=(turn0, turn1))
    before = []
    after = []
    turns = [
        _turn_record(0, answer="Dạ chị muốn đặt giờ nào ạ?", db_after=after),
        _turn_record(1, answer="Dạ em đặt xong rồi ạ", db_after=after),
    ]

    result = score_run(scenario, turns, before, after, CUSTOMER_ID, NOW_VN)

    assert result.valid is False
    assert result.invalid_reason == "precondition@1"


def test_check_precondition_requires_prev_and_mention():
    from bench.scenario import ApptSpec

    turn = Turn(intent="agree", requires_asked=ApptSpec(day="tomorrow", time="15:00"))
    assert check_precondition(turn, None, NOW_VN) is False

    prev_no_mention = _turn_record(0, answer="Dạ vâng ạ")
    assert check_precondition(turn, prev_no_mention, NOW_VN) is False

    prev_error = _turn_record(0, answer="3 giờ chiều 18/10", error="timeout")
    assert check_precondition(turn, prev_error, NOW_VN) is False

    prev_ok = _turn_record(0, answer="Dạ 3 giờ chiều 18/10 nhé")
    assert check_precondition(turn, prev_ok, NOW_VN) is True

    no_req_turn = Turn(say="hi")
    assert check_precondition(no_req_turn, None, NOW_VN) is True


def test_text_rules():
    assert TEXT_RULES["register"]("Dạ cô muốn đặt giờ nào ạ?") is True
    assert TEXT_RULES["english"]("Model call limits exceeded") is True
    assert TEXT_RULES["clock_colon"]("Dạ mình đặt lúc 15:00 nhé") is True

    clean = "Dạ em giữ 3 giờ chiều cho chị nhé"
    assert TEXT_RULES["register"](clean) is False
    assert TEXT_RULES["english"](clean) is False
    assert TEXT_RULES["clock_colon"](clean) is False


def test_claim_without_write():
    scenario = _scenario(Expect(booked=None))
    before = [_row("1", CUSTOMER_ID, datetime(2026, 10, 18, 9, 0, tzinfo=VN_TZ))]
    # Không có thay đổi gì giữa before và db_after của lượt, nhưng answer nhận đã xong.
    turns = [_turn_record(0, answer="Dạ em đặt xong rồi ạ", db_after=list(before))]

    result = score_run(scenario, turns, before, before, CUSTOMER_ID, NOW_VN)

    assert "claim_without_write@0" in result.violations


def test_claim_without_write_not_flagged_when_db_changed():
    scenario = _scenario(Expect(booked=None))
    before = []
    after = [_row("1", CUSTOMER_ID, datetime(2026, 10, 18, 9, 0, tzinfo=VN_TZ))]
    turns = [_turn_record(0, answer="Dạ em đặt xong rồi ạ", db_after=after)]

    result = score_run(scenario, turns, before, after, CUSTOMER_ID, NOW_VN)

    assert result.violations == []


def test_pass_hat_k_and_aggregate():
    assert pass_hat_k([True, True, True], 3) is True
    assert pass_hat_k([True, False, True], 3) is False
    assert pass_hat_k([True, True], 3) is False  # chưa đủ k lần

    scores = [
        RunScore(True, True, None, [], [], 1),
        RunScore(False, True, None, ["extra:09:00 18/10"], ["clock_colon@0"], 2),
        RunScore(False, False, "precondition@1", [], [], 1),  # invalid, loại khỏi mẫu số
    ]
    agg = aggregate(scores, k=3)

    assert agg["runs"] == 3
    assert agg["valid_runs"] == 2
    assert agg["pass_rate"] == 0.5
    assert agg["pass_hat_k_rate"] == 0.0
    assert agg["wrong_writes_total"] == 1
    assert agg["violations_by_rule"] == {"clock_colon": 1}


def test_behavior_match():
    base = [
        _turn_record(0, tools=["list_slots"], db_after=[_row("1", "a", NOW_VN)]),
        _turn_record(
            1,
            tools=["propose_appointment"],
            db_after=[_row("1", "a", NOW_VN), _row("2", "a", NOW_VN)],
        ),
    ]
    cand = [
        _turn_record(0, tools=["list_slots"], db_after=[_row("9", "b", NOW_VN)]),
        _turn_record(
            1,
            tools=["propose_appointment"],
            db_after=[_row("9", "b", NOW_VN), _row("10", "b", NOW_VN)],
        ),
    ]

    assert behavior_match(base, cand) == [True, True]

    cand_diff_tools = [
        _turn_record(0, tools=["list_slots"], db_after=[_row("9", "b", NOW_VN)]),
        _turn_record(1, tools=["cancel_appointment"], db_after=cand[1].db_after),
    ]
    assert behavior_match(base, cand_diff_tools) == [True, False]
