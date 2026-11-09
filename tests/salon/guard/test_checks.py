"""TDD cho `guard/checks.py` (Task 12, spec §7, docs/architech-agent.md luật
4-5): câu trả lời cuối của Orchestrator được đối chiếu bằng code với dữ kiện
của lượt (`ReplyFacts`), trả mã vi phạm theo thứ tự cố định.

Bảng `test_check_reply_table` lấy nguyên các câu của task brief. Ghi chú về
cách dựng một vài dòng (để các mã KHÁC trong bảng không bật theo):

- `claim_before_approve`: "ProposalFact 15:00" của brief dựng thành đề xuất
  ĐANG CHỜ từ lượt trước (khách chưa duyệt) đã `decide_pending(none)` — một
  `ProposalFact` mới trong lượt thì bắt buộc có câu hỏi (`missing_question`),
  mà câu của brief không có dấu "?".
- `hold_without_any`: giờ 15:00 có thật nhờ một `TimeFact` "free" (agent vừa
  thấy giờ trống) — không thì câu còn bị `unknown_time`.
- Các dòng có đề xuất chờ ở lượt gõ chữ đều đã `decide_pending` (trừ
  `pending_ignored`) — không thì mọi dòng đó bị `pending_undecided`.
"""
from __future__ import annotations

import pytest

from app.agents.salon.guard.checks import check_reply, strip_tool_text
from app.agents.salon.shared.schemas import DecisionFact, Moment, ProposalFact, ServiceFact, TimeFact
from app.core.config import settings
from tests.salon.guard import (
    GEL,
    GOI,
    PHONE,
    a_snapshot,
    appointment_at,
    at,
    facts_for,
    pending_at,
    r0,
    seed_catalog,
)

M15 = at(15)          # spoken "3 giờ chiều"
M16 = at(16)          # spoken "4 giờ chiều"
# Cùng 3 giờ chiều, hai ngày khác nhau (guard không so ngày).
TUE_15 = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
WED_15 = Moment(iso="2026-11-11T15:00:00+07:00", spoken="Thứ Tư 11/11, 3 giờ chiều")


@pytest.fixture(autouse=True)
def shop_phone(monkeypatch):
    monkeypatch.setattr(settings, "shop_phone", PHONE)


def _kept(moment=M15) -> DecisionFact:
    return DecisionFact(outcome="kept", proposal_kind="book", at=moment)


def _proposal(moment=M15, *, kind="book", service_ids=()) -> ProposalFact:
    return ProposalFact(proposal_id=f"p-{moment.iso}", proposal_kind=kind, at=moment,
                        service_ids=list(service_ids))


def _service_fact(service=GEL) -> ServiceFact:
    return ServiceFact(service_id=service.id, name=service.name, price_min=service.price_min,
                       price_max=service.price_max, duration_minutes=service.duration_minutes)


ROWS = [
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ.", ["claim"], id="claim_before_approve"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M15)]),
             turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)],
             pending_decided=True),
        "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ.", [], id="claim_after_approve"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True,
             question="tiệm mở cửa mấy giờ vậy em?"),
        "Dạ tiệm mở cửa từ 8 giờ sáng ạ. Lịch 3 giờ chiều mai em vẫn đang giữ cho chị, "
        "chị đồng ý không ạ?", [], id="hold_on_pending"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=M15)]),
        "Dạ em đang giữ 3 giờ chiều mai cho chị ạ.", ["claim"], id="hold_without_any"),
    pytest.param(
        dict(question="mai 3 giờ"),
        "Dạ chị Lan, 3 giờ sáng hay 3 giờ chiều ạ?", [], id="echo_customer_clock"),
    pytest.param(
        dict(turn_facts=[_proposal()]),
        "Em giữ chỗ 4 giờ chiều mai cho chị nhé, chị đồng ý không ạ?",
        ["claim", "unknown_time", "missing_fact"], id="invented_time"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        "Dạ sơn gel khoảng 150 nghìn ạ.", [], id="price_inside_range"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        "Dạ sơn gel 200k ạ.", ["unknown_price"], id="price_outside_range"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        "Dạ từ 120 tới 180 nghìn ạ.", [], id="price_range_inside"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15, service_ids=[GEL.id, GOI.id])),
             turn_facts=[_kept()], pending_decided=True),
        "Tổng khoảng 200 nghìn ạ.", [], id="price_group_total"),
    pytest.param(
        dict(turn_facts=[_proposal()]),
        "Em giữ chỗ Thứ Hai 9/11, 3 giờ chiều cho chị nhé.", ["missing_question"],
        id="proposal_no_question"),
    pytest.param(
        dict(turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15,
                                      error="slot_taken")], pending_decided=True),
        "Dạ 3 giờ chiều mai em chưa ghi được ạ.", ["missing_phone"], id="failed_no_phone"),
    pytest.param(
        dict(turn_facts=[_proposal()]),
        f"Dạ em giữ chỗ 3 giờ chiều mai cho chị, chị đồng ý không ạ? Cần gì chị gọi số {PHONE} nhé.",
        ["phone_not_allowed"], id="phone_when_fine"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15))),
        "Dạ chào chị ạ.", ["missing_fact"], id="r0_missing"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), pending_decided=False),
        "Dạ em cảm ơn chị ạ.", ["pending_undecided"], id="pending_ignored"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ tiệm mở tới 7 giờ tối ạ. Lịch 3 giờ chiều mai em vẫn đang giữ cho chị, "
        "chị đồng ý không ạ?", [], id="pending_decided_none"),
    pytest.param(
        dict(),
        "Dạ cô Lan ơi, tiệm mở cửa từ 8 giờ sáng ạ.", ["pronoun"], id="wrong_pronoun"),
    pytest.param(
        dict(previous=["Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ."]),
        "Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ.", ["repeat"], id="repeat_previous"),
    pytest.param(
        dict(turn_facts=[_proposal(M16)],
             previous=["Dạ em giữ chỗ 3 giờ chiều cho chị, chị đồng ý không ạ?"]),
        "Dạ em giữ chỗ 4 giờ chiều cho chị, chị đồng ý không ạ?", [], id="repeat_with_new_digits"),
    pytest.param(
        dict(turn_kind="button", pending_decided=True,
             turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15),
                         _proposal(M16)]),
        "Em giữ chỗ 4 giờ chiều cho con gái chị, chị đồng ý không ạ?", [],
        id="button_turn_next_proposal"),
    # Bài học Task 8: `scan_times` trả buổi CÓ DẤU ("tối"), dữ kiện đi qua
    # `scan_spoken_clocks` — hai phía phải chuẩn hoá cùng một dạng, không thì
    # "9 giờ tối" lọt qua khi chỉ có 9 giờ sáng (hoặc mọi giờ đều bị loại).
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(9))]),
        "Dạ 9 giờ tối còn trống ạ.", ["unknown_time"], id="evening_vs_morning_clock"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(21))]),
        "Dạ 9 giờ tối còn trống ạ.", [], id="evening_clock_known"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=M15)]),
        "Dạ 15h còn trống ạ.", [], id="machine_clock_matches_spoken"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M16)]),
             turn_facts=[DecisionFact(outcome="approved", proposal_kind="move", at=M16)],
             pending_decided=True, question="dời giúp chị lịch 3 giờ chiều sang 4 giờ chiều"),
        "Dạ em đã dời lịch từ 3 giờ chiều sang 4 giờ chiều cho chị rồi ạ.", [],
        id="move_mentions_old_time"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M15)]),
             turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)],
             pending_decided=True),
        "Dạ em đã huỷ lịch 3 giờ chiều cho chị rồi ạ.", ["claim"], id="claim_wrong_kind"),
    pytest.param(
        dict(snapshot=dict(address="anh chị")),
        "Dạ chị ơi, tiệm mở cửa từ 8 giờ sáng ạ.", ["pronoun"], id="guessed_gender"),
    pytest.param(
        dict(snapshot=dict(address="anh chị")),
        "Dạ anh chị ơi, tiệm mở cửa từ 8 giờ sáng ạ.", [], id="anh_chi_kept"),
    pytest.param(
        dict(snapshot=dict(address="anh Tám")),
        "Dạ chị ơi, tiệm mở cửa từ 8 giờ sáng ạ.", ["pronoun"], id="wrong_gender"),
    pytest.param(
        dict(),
        "Để con xem lịch giúp chị nhé.", ["pronoun"], id="con_as_speaker"),
    # --- fix round 1 -------------------------------------------------------
    # Ruling R8: kể lại một lịch khách ĐANG CÓ không phải nhận vơ — nhưng chỉ
    # với động từ đặt / giữ, không bao giờ với dời / huỷ.
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M15)])),
        "Dạ chị đã đặt lịch 3 giờ chiều mai rồi ạ.", [], id="existing_booking_described"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M15)])),
        "Dạ em vẫn giữ lịch 3 giờ chiều mai cho chị ạ.", [], id="existing_booking_held"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(M15)])),
        "Dạ em đã dời sang 3 giờ chiều cho chị rồi ạ.", ["claim"], id="existing_is_not_a_move"),
    # Lượt nút bấm: `button_reply` đã báo thất bại kèm số — không bắt lại.
    pytest.param(
        dict(turn_kind="button", pending_decided=True,
             turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15)]),
        "Dạ chị cần gì thêm cứ nhắn em nhé.", [], id="button_failed_needs_no_phone"),
    # R0 thất bại = ghi thất bại chưa báo: câu báo phải có số điện thoại tiệm.
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15, outcome="failed"))),
        "Dạ lịch 3 giờ chiều hôm trước em chưa ghi được ạ.", ["missing_phone"],
        id="r0_failed_no_phone"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15, outcome="failed"))),
        f"Dạ lịch 3 giờ chiều hôm trước em chưa ghi được ạ, chị gọi giúp em số {PHONE} nhé.", [],
        id="r0_failed_with_phone"),
    # Phủ định chỉ tính khi đứng ngay trước động từ.
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ em đã đặt xong 3 giờ chiều rồi chị không phải lo ạ.", ["claim"],
        id="negation_later_in_clause"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ em chưa đặt 3 giờ chiều đâu ạ, chị đồng ý không ạ?", [], id="negation_before_verb"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ em đã xác nhận lịch 3 giờ chiều cho chị rồi ạ.", ["claim"], id="xac_nhan_is_a_claim"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), turn_facts=[_kept()], pending_decided=True),
        "Dạ em đã lên lịch 3 giờ chiều cho chị rồi ạ.", ["claim"], id="len_lich_is_a_claim"),
    # Giờ so theo giá trị trong ngày, không theo nhãn buổi.
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(18))]),
        "Dạ 6 giờ chiều còn trống ạ.", [], id="six_pm_said_as_chieu"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(11))]),
        "Dạ 11 giờ trưa còn trống ạ.", [], id="eleven_am_said_as_trua"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=M15)]),
        "Dạ 3 giờ sáng còn trống ạ.", ["unknown_time"], id="morning_is_not_afternoon"),
    # --- fix round 2 -------------------------------------------------------
    # Lịch đang có chỉ bảo chứng khi mệnh đề NÊU giờ, và giờ đó không đồng
    # thời là giờ của một đề xuất chờ / đề xuất mới.
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15), upcoming=[appointment_at(at(10))]),
             turn_facts=[_kept()], pending_decided=True),
        "Dạ em đã đặt xong lịch cho chị rồi ạ.", ["claim"], id="existing_never_vouches_clockless"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(at(10))])),
        "Dạ em đang giữ chỗ cho chị ạ.", ["claim"], id="clockless_hold_needs_a_real_hold"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(WED_15), upcoming=[appointment_at(TUE_15)]),
             turn_facts=[_kept(WED_15)], pending_decided=True),
        "Dạ em đã chốt lịch 3 giờ chiều thứ Tư cho chị rồi ạ.", ["claim"],
        id="existing_does_not_vouch_a_pending_clock"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(TUE_15)]), turn_facts=[_proposal(WED_15)]),
        "Dạ em đã đặt lịch Thứ Tư 11/11, 3 giờ chiều cho chị rồi, chị đồng ý không ạ?", ["claim"],
        id="existing_does_not_vouch_a_new_proposal_clock"),
    # Câu hỏi "… chưa ạ?" không phải nhận đã làm.
    pytest.param(
        dict(question="mai 4 giờ chiều còn chỗ không em"),
        "Dạ chị đã đặt lịch 4 giờ chiều chưa ạ?", [], id="question_ending_in_chua"),
    pytest.param(
        dict(),
        "Dạ em đã xác nhận thông tin của chị rồi ạ.", [], id="xac_nhan_without_appointment"),
]


@pytest.mark.parametrize("setup, reply, expected", ROWS)
async def test_check_reply_table(test_db, setup, reply, expected):
    await seed_catalog(test_db)
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    facts = await facts_for(test_db, snapshot=snapshot, **setup)
    assert check_reply(reply, facts) == expected


async def test_must_mention_is_verbatim(test_db):
    """`spoken` thật ("Thứ Ba 10/11, 3 giờ chiều") phải có NGUYÊN VĂN trong câu —
    chỉ nói "3 giờ chiều mai" là thiếu (model được dặn trích nguyên văn)."""
    full = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
    proposal = ProposalFact(proposal_id="p-1", proposal_kind="book", at=full, service_ids=[])
    facts = await facts_for(test_db, turn_facts=[proposal])
    assert check_reply("Em giữ chỗ 3 giờ chiều mai cho chị, chị đồng ý không ạ?", facts) == [
        "missing_fact"]
    assert check_reply("Em giữ chỗ Thứ Ba 10/11, 3 giờ chiều cho chị, chị đồng ý không ạ?",
                       facts) == []


async def test_pending_undecided_only_on_typed_turns(test_db):
    """Lượt nút bấm: code đã quyết đề xuất — không bao giờ `pending_undecided`."""
    snapshot = a_snapshot(pending=pending_at(M15))
    typed = await facts_for(test_db, snapshot=snapshot, pending_decided=False)
    button = await facts_for(test_db, snapshot=snapshot, turn_kind="button", pending_decided=False)
    assert typed.pending_undecided is True
    assert button.pending_undecided is False


async def test_button_turn_does_not_require_decision_spoken(test_db):
    facts = await facts_for(
        test_db, turn_kind="button", pending_decided=True,
        turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)],
    )
    assert facts.must_mention == []
    assert facts.done_claims["booked"]


async def test_pending_outcome_is_the_fallback_source_of_the_decision(test_db):
    """Không có `DecisionFact` trong `turn_facts` (lượt rơi về câu mẫu sau lỗi)
    → quyết định đọc từ `pending_outcome`."""
    from app.agents.salon.shared.schemas import PendingOutcome

    outcome = PendingOutcome(outcome="approved", proposal_id="p-1", kind="book", at=M15)
    facts = await facts_for(test_db, pending_outcome=outcome, pending_decided=True)
    assert facts.done_claims["booked"]
    assert "3 giờ chiều" in facts.must_mention


def test_strip_tool_text_removes_names_and_json():
    dirty = ('Dạ em đã xem (appointment_task) rồi ạ. {"status": "ok", "facts": {"a": 1}} '
             'Lịch `propose_appointment()` 3 giờ chiều còn trống, decide_pending chị nhé.')
    assert strip_tool_text(dirty) == "Dạ em đã xem rồi ạ. Lịch 3 giờ chiều còn trống, chị nhé."


def test_strip_tool_text_keeps_clean_text_untouched():
    clean = "Dạ em giữ chỗ 3 giờ chiều cho chị,\nchị đồng ý không ạ?"
    assert strip_tool_text(clean) == clean


def test_strip_tool_text_keeps_words_that_only_contain_a_tool_name():
    text = "Dạ my_parse_time_x không phải tên tool ạ."
    assert strip_tool_text(text) == text


async def test_tool_name_set_covers_every_subagent_tool(test_db):
    """Tập tên dùng chung trong `shared/` phải phủ mọi tool agent con dựng ra
    — thêm tool mà quên tập này thì tên tool lọt tới khách."""
    from app.agents.salon.orchestrator import tools as orchestrator_tools
    from app.agents.salon.shared.tool_names import CONTROL_TOOLS, SUBAGENT_TOOLS
    from app.agents.salon.subagents.appointment.agent import APPOINTMENT
    from app.agents.salon.subagents.dispatch import RunContext
    from app.agents.salon.subagents.shop_info.agent import SHOP_INFO
    from tests.salon.subagents import a_holder, a_user

    user = await a_user(test_db)
    ctx = RunContext(db=test_db, user=user, snapshot=await a_holder(test_db, user).current(),
                     session=None, request="", question="", turn_id="turn-1", service_ids=[],
                     allow_propose=True)
    names = {t.name for spec in (APPOINTMENT, SHOP_INFO) for t in spec.build_tools(ctx)}
    assert names <= SUBAGENT_TOOLS
    assert orchestrator_tools.CONTROL_TOOLS is CONTROL_TOOLS
