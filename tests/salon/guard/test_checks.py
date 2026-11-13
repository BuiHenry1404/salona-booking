"""TDD cho `guard/checks.py` (Task 12, spec §7, docs/architech-agent.md luật
4-5): câu trả lời cuối của Orchestrator được đối chiếu bằng code với dữ kiện
của lượt (`ReplyFacts`), trả mã vi phạm theo thứ tự cố định.

Bảng `test_check_text_table` (luật đọc trên chữ, `check_text`) và
`test_check_structured_table` (giờ / giá / dữ kiện bắt buộc, `check_structured`
với câu JSON các đoạn trích dẫn — level C) lấy nguyên các câu của task brief
Task 12; câu đã ghép của mỗi dòng cấu trúc bằng đúng câu cũ. Ghi chú về cách
dựng một vài dòng (để các mã KHÁC trong bảng không bật theo):

- `claim_before_approve`: "ProposalFact 15:00" của brief dựng thành đề xuất
  ĐANG CHỜ từ lượt trước (khách chưa duyệt) đã `decide_pending(none)` — một
  `ProposalFact` mới trong lượt thì bắt buộc có câu hỏi (`missing_question`),
  mà câu của brief không có dấu "?".
- `hold_without_any`: giờ 15:00 có thật nhờ một `TimeFact` "free" (agent vừa
  thấy giờ trống).
- Các dòng có đề xuất chờ ở lượt gõ chữ đều đã `decide_pending` (trừ
  `pending_ignored`) — không thì mọi dòng đó bị `pending_undecided`.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.agents.salon.guard.checks import (
    CHECK_CODES,
    MEANING_CODES,
    STRUCTURED_CODES,
    check_reply,
    check_structured,
    check_text,
    strip_tool_text,
)
from app.agents.salon.guard.facts import ReplyFacts
from app.agents.salon.orchestrator.prompt import ORCHESTRATOR_PROMPT
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
    UnannouncedDecision,
)
from app.agents.salon.subagents.appointment.prompt import APPOINTMENT_PROMPT
from app.agents.salon.subagents.service.prompt import SERVICE_PROMPT
from app.agents.salon.subagents.shop_info.prompt import SHOP_INFO_PROMPT
from app.core.clock import TZ
from app.core.config import settings
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService
from tests.salon.guard import (
    CAT,
    GEL,
    GOI,
    PHONE,
    TURN_ID,
    a_session,
    a_snapshot,
    appointment_at,
    at,
    facts_for,
    pending_at,
    r0,
    seed_catalog,
)
from tests.salon.guard.test_plan import PORTED_ROWS

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


def _raw(facts: ReplyFacts, *parts) -> str:
    """Dựng JSON thô của câu trả lời cấu trúc (Task 3 brief, bước 1) — mỗi
    phần là chữ thường (`str`), hoặc `(text, target, role)` với `target` là
    một `Moment` / `Service` (tra `facts.catalog` ra id) hay một `str` dùng
    thẳng làm `ref`; `target=None` là đoạn không trích (`ref`/`role` đều
    `None`)."""
    segments = []
    for part in parts:
        if isinstance(part, str):
            segments.append({"text": part, "ref": None, "role": None})
            continue
        text, target, role = part
        if target is None:
            ref = None
        elif isinstance(target, Moment):
            ref = facts.catalog.id_of(target)
        elif isinstance(target, Service):
            entry = next(e for e in facts.catalog.entries if e.service_ids == [target.id])
            ref = entry.id
        else:
            ref = target
        segments.append({"text": text, "ref": ref, "role": role})
    return json.dumps({"segments": segments})


# Luật đọc trên CHỮ (`check_text`): claim, pronoun, phone, question, repeat,
# pending — không phụ thuộc đoạn trích dẫn.
TEXT_ROWS = [
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
    # --- fix round 3 (Ruling R9) -------------------------------------------
    # Lịch đang có chỉ bảo chứng giờ KHÔNG có ở nguồn dữ kiện nào khác của
    # lượt (guard không so ngày).
    pytest.param(
        dict(snapshot=dict(pending=pending_at(WED_15), upcoming=[appointment_at(TUE_15)]),
             turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=WED_15)],
             pending_decided=True),
        f"Dạ em đã đặt xong lịch Thứ Tư 11/11, 3 giờ chiều cho chị rồi ạ, có gì chị gọi số {PHONE} nhé.",
        ["claim"], id="existing_does_not_vouch_a_failed_pending_clock"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(WED_15), upcoming=[appointment_at(TUE_15)]),
             turn_facts=[DecisionFact(outcome="rejected", proposal_kind="book", at=WED_15)],
             pending_decided=True),
        "Dạ em đã đặt xong lịch Thứ Tư 11/11, 3 giờ chiều cho chị rồi ạ.", ["claim"],
        id="existing_does_not_vouch_a_rejected_pending_clock"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(TUE_15)]),
             turn_facts=[TimeFact(role="free", moment=WED_15)]),
        "Dạ em đã chốt 3 giờ chiều cho chị rồi ạ.", ["claim"],
        id="existing_does_not_vouch_a_free_clock"),
    pytest.param(
        dict(snapshot=dict(upcoming=[appointment_at(TUE_15)]),
             turn_facts=[TimeFact(role="appointment", moment=TUE_15)]),
        "Dạ em đã chốt 3 giờ chiều cho chị rồi ạ.", [], id="existing_alone_still_vouches"),
]


def _codes(found: set[str]) -> list[str]:
    return [code for code in CHECK_CODES if code in found]


@pytest.mark.parametrize("setup, reply, expected", TEXT_ROWS)
async def test_check_text_table(test_db, setup, reply, expected):
    await seed_catalog(test_db)
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    facts = await facts_for(test_db, snapshot=snapshot, **setup)
    assert _codes(check_text(reply, facts)) == expected


G1 = "g1"   # id nhóm đầu tiên của danh mục (đề xuất chờ nhiều dịch vụ)

# Giờ / giá / dữ kiện bắt buộc (`check_structured`): mỗi giờ, tên dịch vụ, giá
# là một đoạn trích dẫn. Trước level C các dòng này so trên chữ (`unknown_time`
# / `unknown_price` / `missing_fact` nguyên văn); giờ:
# - giờ / giá không có dữ kiện nào → đoạn chữ thường, mã `unreferenced`;
# - giờ / giá sai so với dữ kiện có thật → trích dữ kiện đó, mã `ref_mismatch`;
# - dữ kiện bắt buộc không được trích → `missing_fact`.
STRUCTURED_ROWS = [
    pytest.param(
        dict(question="mai 3 giờ"),
        ("Dạ chị Lan, ", ("3 giờ sáng", "c1", "asked"), " hay ", ("3 giờ chiều", "c1", "asked"),
         " ạ?"), [], id="echo_customer_clock"),
    pytest.param(
        dict(turn_facts=[_proposal()]),
        ("Em giữ chỗ 4 giờ chiều mai cho chị nhé, chị đồng ý không ạ?",),
        ["unreferenced", "claim", "missing_fact"], id="invented_time"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        ("Dạ ", ("sơn gel", GEL, "service"), " ", ("khoảng 150 nghìn", GEL, "price"), " ạ."), [],
        id="price_inside_range"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        ("Dạ ", ("sơn gel", GEL, "service"), " ", ("200k", GEL, "price"), " ạ."), ["ref_mismatch"],
        id="price_outside_range"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        ("Dạ ", ("từ 120 tới 180 nghìn", GEL, "price"), " ạ."), [], id="price_range_inside"),
    # Live run 1 (11/11): dấu "." ngăn hàng nghìn từng bị coi là hết mệnh đề.
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        ("Dạ ", ("sơn gel", GEL, "service"), " giá ", ("từ 120.000đ đến 180.000đ", GEL, "price"),
         " ạ."), [], id="price_thousands_separator"),
    pytest.param(
        dict(turn_facts=[_service_fact()]),
        ("Dạ ", ("sơn gel", GEL, "service"), " giá ", ("200.000đ", GEL, "price"), " ạ."),
        ["ref_mismatch"], id="price_thousands_outside"),
    # F2 (owner 11/11): a taken time found this turn must be cited.
    pytest.param(
        dict(turn_facts=[TimeFact(role="asked", moment=M15), TimeFact(role="taken", moment=M15),
                         TimeFact(role="free", moment=M16)]),
        ("Dạ chị chọn ", ("4 giờ chiều", M16, "offer"), " được không ạ?"), ["missing_fact"],
        id="taken_time_not_mentioned"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="asked", moment=M15), TimeFact(role="taken", moment=M15),
                         TimeFact(role="free", moment=M16)]),
        ("Dạ ", ("3 giờ chiều", M15, "taken"), " đã có khách rồi ạ, chị chọn ",
         ("4 giờ chiều", M16, "offer"), " được không ạ?"), [], id="taken_time_mentioned"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="taken", moment=M15), _proposal(M16)]),
        ("Dạ em giữ chỗ ", ("4 giờ chiều", M16, "held"), " cho chị, chị đồng ý không ạ?"),
        ["missing_fact"], id="taken_with_proposal_needs_mention"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15, service_ids=[GEL.id, GOI.id])),
             turn_facts=[_kept()], pending_decided=True),
        ("Tổng ", ("khoảng 200 nghìn", G1, "price"), " ạ."), [], id="price_group_total"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15))),
        ("Dạ chào chị ạ.",), ["missing_fact"], id="r0_missing"),
    # Bài học Task 8: `scan_times` trả buổi CÓ DẤU ("tối"), dữ kiện đi qua
    # `scan_spoken_clocks` — hai phía phải chuẩn hoá cùng một dạng, không thì
    # "9 giờ tối" lọt qua khi chỉ có 9 giờ sáng (hoặc mọi giờ đều bị loại).
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(9))]),
        ("Dạ ", ("9 giờ tối", at(9), "offer"), " còn trống ạ."), ["ref_mismatch"],
        id="evening_vs_morning_clock"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(21))]),
        ("Dạ ", ("9 giờ tối", at(21), "offer"), " còn trống ạ."), [], id="evening_clock_known"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=M15)]),
        ("Dạ ", ("15h", M15, "offer"), " còn trống ạ."), [], id="machine_clock_matches_spoken"),
    # Giờ so theo giá trị trong ngày, không theo nhãn buổi.
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(18))]),
        ("Dạ ", ("6 giờ chiều", at(18), "offer"), " còn trống ạ."), [], id="six_pm_said_as_chieu"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=at(11))]),
        ("Dạ ", ("11 giờ trưa", at(11), "offer"), " còn trống ạ."), [],
        id="eleven_am_said_as_trua"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="free", moment=M15)]),
        ("Dạ ", ("3 giờ sáng", M15, "offer"), " còn trống ạ."), ["ref_mismatch"],
        id="morning_is_not_afternoon"),
]


@pytest.mark.parametrize("setup, parts, expected", STRUCTURED_ROWS)
async def test_check_structured_table(test_db, setup, parts, expected):
    await seed_catalog(test_db)
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    facts = await facts_for(test_db, snapshot=snapshot, **setup)
    result = check_structured(_raw(facts, *parts), facts)
    assert result.codes == expected
    assert result.text == "".join(p if isinstance(p, str) else p[0] for p in parts)


async def test_required_fact_is_cited_not_copied_verbatim(test_db):
    """Trước level C `spoken` thật ("Thứ Ba 10/11, 3 giờ chiều") phải có NGUYÊN
    VĂN trong câu. Giờ phải TRÍCH DẪN: chép nguyên văn vào chữ thường không
    đủ (`unreferenced` + `missing_fact`), nói gọn "3 giờ chiều mai" kèm id thì đủ."""
    full = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
    proposal = ProposalFact(proposal_id="p-1", proposal_kind="book", at=full, service_ids=[])
    facts = await facts_for(test_db, turn_facts=[proposal])
    copied = _raw(facts, "Em giữ chỗ Thứ Ba 10/11, 3 giờ chiều cho chị, chị đồng ý không ạ?")
    assert check_structured(copied, facts).codes == ["unreferenced", "missing_fact"]
    cited = _raw(facts, "Em giữ chỗ ", ("3 giờ chiều mai", full, "held"),
                 " cho chị, chị đồng ý không ạ?")
    assert check_structured(cited, facts).codes == []


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
    assert facts.required == []
    assert facts.done_claims["booked"]


async def test_pending_outcome_is_the_fallback_source_of_the_decision(test_db):
    """Không có `DecisionFact` trong `turn_facts` (lượt rơi về câu mẫu sau lỗi)
    → quyết định đọc từ `pending_outcome`."""
    from app.agents.salon.shared.schemas import PendingOutcome

    outcome = PendingOutcome(outcome="approved", proposal_id="p-1", kind="book", at=M15)
    facts = await facts_for(test_db, pending_outcome=outcome, pending_decided=True)
    assert facts.done_claims["booked"]
    assert [(r.ref, r.roles) for r in facts.required] == [(facts.catalog.id_of(M15), ["booked"])]


def test_strip_tool_text_removes_names_and_json():
    dirty = ('Dạ em đã xem (appointment_task) rồi ạ. {"status": "ok", "facts": {"a": 1}} '
             'Lịch `propose_appointment()` 3 giờ chiều còn trống, decide_pending chị nhé.')
    assert strip_tool_text(dirty) == "Dạ em đã xem rồi ạ. Lịch 3 giờ chiều còn trống, chị nhé."


def test_strip_tool_text_keeps_clean_text_untouched():
    clean = "Dạ em giữ chỗ 3 giờ chiều cho chị,\nchị đồng ý không ạ?"
    assert strip_tool_text(clean) == clean


def test_strip_tool_text_removes_markdown_emphasis():
    # Live run 2: 8/10 replies had **...**; the chat shows raw characters.
    text = ("Dạ chị Lan, em đã giữ giờ **Thứ Năm 12/11, 4 giờ chiều** cho __sơn gel__ rồi ạ.  \n"
            "Chị đồng ý giờ này nhé?")
    assert strip_tool_text(text) == ("Dạ chị Lan, em đã giữ giờ Thứ Năm 12/11, 4 giờ chiều cho "
                                     "sơn gel rồi ạ.\nChị đồng ý giờ này nhé?")


def test_strip_tool_text_removes_an_unpaired_bold_marker():
    assert strip_tool_text("Dạ tiệm mở từ **8 giờ sáng ạ.") == "Dạ tiệm mở từ 8 giờ sáng ạ."


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
    from app.agents.salon.subagents.dispatch import tools_for
    from app.agents.salon.subagents.service.agent import SERVICE
    from app.agents.salon.subagents.shop_info.agent import SHOP_INFO
    from tests.salon.subagents import a_holder, a_user, seed_mock_catalogue

    await seed_mock_catalogue(test_db)    # có dịch vụ thì `get_service` mới được dựng
    user = await a_user(test_db)
    ctx = RunContext(db=test_db, user=user, snapshot=await a_holder(test_db, user).current(),
                     session=None, request="", question="", turn_id="turn-1", service_ids=[],
                     allow_propose=True)
    names = {t.name for spec in (APPOINTMENT, SHOP_INFO, SERVICE)
             for t in await tools_for(spec, ctx)}
    assert {"search_services", "get_service", "list_services"} <= names
    assert names <= SUBAGENT_TOOLS
    assert orchestrator_tools.CONTROL_TOOLS is CONTROL_TOOLS


@pytest.mark.parametrize("text", ["", "   ", "\n"])
async def test_empty_reply_is_a_violation(test_db, text):
    """Câu rỗng (vd model viết tool call thành chữ, `strip_tool_text` xoá sạch)
    không bao giờ được gửi khách — mã `empty` đứng đầu."""
    facts = await facts_for(test_db)
    assert "empty" in check_text(text, facts)
    assert check_structured(_raw(facts, text), facts).codes[0] == "empty"
    assert strip_tool_text('decide_pending({"label": "approve"})') == ""


# ---------------------------------------------------------------------------
# Task 3: facts.required + check_structured (level C)
# ---------------------------------------------------------------------------


async def test_required_refs_cover_proposal_taken_r0_and_decision(test_db):
    await seed_catalog(test_db)
    snapshot = a_snapshot(unannounced=r0(at(9)))
    turn_facts = [
        _proposal(M16),
        TimeFact(role="taken", moment=at(15)),
        DecisionFact(outcome="failed", proposal_kind="book", at=at(10)),
    ]
    facts = await facts_for(test_db, snapshot=snapshot, turn_facts=turn_facts)
    id9, id16, id15, id10 = (facts.catalog.id_of(m) for m in (at(9), M16, at(15), at(10)))
    assert [(r.ref, r.roles) for r in facts.required] == [
        (id9, ["booked"]), (id16, ["held"]), (id15, ["taken"]), (id10, ["failed"]),
    ]


async def test_button_turn_does_not_require_the_decision(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(
        test_db, turn_kind="button", pending_decided=True,
        turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)],
    )
    assert facts.required == []


async def test_check_structured_bad_json(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db)
    result = check_structured("Dạ có ạ.", facts)
    assert (result.text, result.codes) == ("", ["bad_structure"])


async def test_empty_segment_list_is_empty(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db)
    assert check_structured('{"segments": []}', facts).codes == ["empty"]
    assert check_structured(_raw(facts, ("  ", None, None)), facts).codes == ["empty"]


async def test_short_phrase_satisfies_a_required_ref(test_db):
    await seed_catalog(test_db)
    moment = Moment(iso=M16.iso, spoken="Thứ Năm 12/11, 4 giờ chiều")
    facts = await facts_for(test_db, turn_facts=[_proposal(moment)])
    raw = _raw(facts, ("4 giờ chiều mai", moment, "held"), "Chị đồng ý không ạ?")
    assert check_structured(raw, facts).codes == []


async def test_missing_fact_when_the_proposal_is_not_cited(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db, turn_facts=[_proposal()])
    raw = _raw(facts, ("Dạ chị đợi em chút nhé?", None, None))
    assert check_structured(raw, facts).codes == ["missing_fact"]


async def test_check_structured_flags_taken_not_told(test_db):
    """Live run 5: a taken time cited correctly as `taken`, but the sentence
    does not tell the customer it is already booked and invites them to
    choose it — `taken_not_told` must fire with a problem line naming the ref."""
    await seed_catalog(test_db)
    facts = await facts_for(test_db, turn_facts=[TimeFact(role="taken", moment=M15)])
    id15 = facts.catalog.id_of(M15)
    raw = _raw(facts, "Dạ chị chọn giúp em ", (M15.spoken, M15, "taken"),
               " nhé. Chị chọn giúp em 1 giờ chiều ạ.")
    result = check_structured(raw, facts)
    assert "taken_not_told" in result.codes
    assert (f"{id15} is cited as taken but the sentence does not tell the customer it is "
            "already booked, or invites them to choose it") in result.problems


async def test_required_taken_todo_says_own_sentence(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db, turn_facts=[TimeFact(role="taken", moment=M15)])
    id15 = facts.catalog.id_of(M15)
    [item] = [r for r in facts.required if r.ref == id15]
    assert item.todo.startswith("already booked by another customer")
    raw = _raw(facts, "Dạ chào chị ạ.")
    result = check_structured(raw, facts)
    assert f"{id15}: {item.todo}" in result.problems


async def test_announce_ids_come_from_cited_decisions(test_db):
    await seed_catalog(test_db)
    snapshot = a_snapshot(unannounced=r0(at(9)))
    facts = await facts_for(test_db, snapshot=snapshot, turn_facts=[TimeFact(role="free", moment=at(11))])
    id9 = facts.catalog.id_of(at(9))
    id11 = facts.catalog.id_of(at(11))

    raw_booked = _raw(facts, ("9 giờ sáng", id9, "booked"), "Dạ chị nhé.")
    assert check_structured(raw_booked, facts).announce_turn_ids == ["turn-0"]

    raw_offer = _raw(facts, ("11 giờ sáng", id11, "offer"), "Dạ chị nhé.")
    assert check_structured(raw_offer, facts).announce_turn_ids == []


async def test_check_structured_orders_codes(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db, turn_facts=[TimeFact(role="free", moment=M15)])
    id15 = facts.catalog.id_of(M15)
    raw = _raw(facts, ("3 giờ chiều mai", id15, "held"), "Dạ em đang giữ chỗ cho chị ạ.")
    result = check_structured(raw, facts)
    assert result.codes == ["wrong_role", "claim"]


async def test_check_text_has_no_old_text_rules(test_db):
    await seed_catalog(test_db)
    facts = await facts_for(test_db)
    assert "unknown_time" not in check_text("Dạ còn 5 giờ chiều ạ.", facts)
    assert not {"unknown_time", "unknown_price"} & set(STRUCTURED_CODES)


# ---------------------------------------------------------------------------
# Task 4: foreign_script / english
# ---------------------------------------------------------------------------

LIVE_REPLIES = [  # verbatim model replies of live runs 3 and 4 (.superpowers/sdd/2026-11-08-subagents-rebuild/live-{3,4}.log)
    "Dạ em chào chị Lan ạ, rất vui được đón chị lần đầu qua tiệm. Chị cần em hỗ trợ gì cứ nhắn em nhé.",
    "Dạ chị Lan ơi, lúc Thứ Năm 12/11, 3 giờ chiều đã có người đặt rồi ạ. Chị chọn giúp em 1 giờ chiều, 2 giờ chiều, hoặc 4 giờ chiều nhé.",
    "Dạ chị Lan, tiệm mình mở từ 8 giờ sáng đến 7 giờ tối ạ.",
    "Dạ chị Lan, nhuộm tóc giá từ 350.000đ đến 700.000đ ạ.",
    "Dạ em chào chị Mai ạ. Chị muốn làm tóc hay làm móng, em giúp chị ngay nhé.",
    "Dạ chị Mai, Chủ Nhật 15/11 tiệm mở cửa bình thường ạ. Nếu chị muốn, em xem giúp giờ phù hợp cho chị.",
    "Dạ chị Mai, chị có 2 lịch: Thứ Năm 12/11, 11 giờ sáng và Thứ Năm 12/11, 5 giờ chiều ạ.",
]
ARMENIAN = ("Dạ chị Mai, sơn gel bên em giá từ 120.000đ đến 180.000đ ạ. Եթե chị muốn, "
            "em đặt giúp chị giờ khác nhé.")


async def test_foreign_script_blocks_the_live_run_4_reply(test_db):
    facts = await facts_for(test_db)
    assert "foreign_script" in check_text(ARMENIAN, facts)


@pytest.mark.parametrize("reply", LIVE_REPLIES)
async def test_live_replies_are_not_foreign_or_english(test_db, reply):
    facts = await facts_for(test_db)
    found = check_text(reply, facts)
    assert "foreign_script" not in found
    assert "english" not in found


@pytest.mark.parametrize("reply", LIVE_REPLIES)
async def test_nfd_vietnamese_is_not_foreign_or_english(test_db, reply):
    import unicodedata as _ud

    facts = await facts_for(test_db)
    found = check_text(_ud.normalize("NFD", reply), facts)
    assert "foreign_script" not in found
    assert "english" not in found


async def test_english_sentence_is_blocked(test_db):
    facts = await facts_for(test_db)
    assert "english" in check_text("Sorry, I will check the schedule for you.", facts)


@pytest.mark.parametrize("prompt", [ORCHESTRATOR_PROMPT, APPOINTMENT_PROMPT, SERVICE_PROMPT,
                                    SHOP_INFO_PROMPT])
async def test_a_copied_prompt_line_is_blocked(test_db, prompt):
    facts = await facts_for(test_db)
    first_line = next(line for line in prompt.splitlines() if line.strip())
    reply = f"Dạ chị Lan ơi, {first_line} ạ."
    assert "english" in check_text(reply, facts)


# ---------------------------------------------------------------------------
# Plan-guided reply, Task 2: `check_reply` đọc CHỮ THƯỜNG của model và đối chiếu
# với kế hoạch câu (`facts.plan`) cùng dữ kiện của lượt.
# ---------------------------------------------------------------------------

# Hôm nay của `a_snapshot` là 9/11; `at()` là 10/11, tức "mai".
TODAY_15 = Moment(iso=datetime(2026, 11, 9, 15, 0, tzinfo=TZ).isoformat(),
                  spoken="Thứ Hai 9/11, 3 giờ chiều")

TAKEN15_OPTIONS = dict(
    snapshot=dict(sessions={"appointment": a_session(field="time", opened_turn_id=TURN_ID,
                                                     options=[at(13), at(14), at(17)])}),
    turn_facts=[TimeFact(role="taken", moment=M15)])
TAKEN15_OPTIONS_PLUS_GEL_PRICE = dict(
    snapshot=TAKEN15_OPTIONS["snapshot"],
    turn_facts=[*TAKEN15_OPTIONS["turn_facts"],
                ServiceFact(service_id=GEL.id, name=GEL.name, price_min=GEL.price_min,
                            price_max=GEL.price_max, duration_minutes=GEL.duration_minutes,
                            identified=True)])
PROPOSAL_CUT14 = dict(
    snapshot=dict(address="chị Mai"),
    turn_facts=[ProposalFact(proposal_id="p-2", proposal_kind="book", at=at(14),
                             service_ids=[CAT.id])])
TAKEN15_TODAY_OFFER15_TOMORROW = dict(
    turn_facts=[TimeFact(role="taken", moment=TODAY_15), TimeFact(role="free", moment=M15)])
SESSION_SERVICE_QUESTION = dict(
    snapshot=dict(sessions={"appointment": a_session(field="service", opened_turn_id=TURN_ID)}))
NO_FACTS: dict = dict()

CASES = [  # (id, setup kwargs for facts_for, reply text, expected codes)
    ("taken_offer_ok", TAKEN15_OPTIONS,
     "Dạ chị Lan ơi, 3 giờ chiều mai bên em có khách mất rồi ạ. Chị lấy giúp em "
     "1 giờ, 2 giờ hay 5 giờ chiều mai được không ạ?", []),
    ("live5_b_turn7", TAKEN15_OPTIONS,
     "Dạ chị chọn giúp em 3 giờ chiều mai nhé. Chị chọn giúp em 1 giờ chiều, "
     "2 giờ chiều hoặc 5 giờ chiều ạ.", ["taken_offered"]),
    ("missing_option", TAKEN15_OPTIONS,
     "Dạ 3 giờ chiều mai có khách rồi ạ, chị lấy 1 giờ hay 2 giờ chiều nhé?", ["plan_missing"]),
    ("invented_time", TAKEN15_OPTIONS,
     "Dạ 3 giờ chiều mai có khách rồi ạ, chị lấy 1 giờ, 2 giờ hay 4 giờ chiều mai nhé?",
     ["plan_missing", "unknown_time"]),
    ("live4_turn8_wrong_service", PROPOSAL_CUT14,
     "Dạ em đã giữ chỗ sơn gel lúc 2 giờ chiều mai cho chị Mai, chị đồng ý nhé?",
     ["wrong_service"]),
    ("proposal_ok", PROPOSAL_CUT14,
     "Dạ em giữ chỗ cắt tóc nữ 2 giờ chiều mai cho chị nha, chị đồng ý không ạ?", []),
    ("two_requests_ok", TAKEN15_OPTIONS_PLUS_GEL_PRICE,
     "Dạ 3 giờ chiều mai bên em có khách rồi ạ. Chị lấy giúp em 1 giờ, 2 giờ hay 5 giờ chiều "
     "mai nhé? Sơn gel bên em giá khoảng 150 nghìn ạ.", []),
    # Lượt đặt lịch: giá không bắt buộc (kế hoạch không có hành động báo giá),
    # nhưng giá nêu ra vẫn phải nằm trong khoảng.
    ("two_requests_price_optional", TAKEN15_OPTIONS_PLUS_GEL_PRICE,
     "Dạ 3 giờ chiều mai bên em có khách rồi ạ. Chị lấy giúp em 1 giờ, 2 giờ hay 5 giờ chiều "
     "mai nhé?", []),
    ("two_requests_wrong_price", TAKEN15_OPTIONS_PLUS_GEL_PRICE,
     "Dạ 3 giờ chiều mai bên em có khách rồi ạ. Chị lấy giúp em 1 giờ, 2 giờ hay 5 giờ chiều "
     "mai nhé? Sơn gel giá 250 nghìn ạ.", ["unknown_price"]),
    ("same_clock_other_day", TAKEN15_TODAY_OFFER15_TOMORROW, "Dạ chị chọn 3 giờ chiều mai nhé?", []),
    ("question_only_plan", SESSION_SERVICE_QUESTION, "Dạ chị muốn làm gì để em xếp lịch ạ?", []),
    ("greeting_no_plan", NO_FACTS, "Dạ em chào chị Lan ạ.", []),
]


async def _reply_facts(db, setup: dict) -> ReplyFacts:
    await seed_catalog(db)
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    return await facts_for(db, snapshot=snapshot, **setup)


@pytest.mark.parametrize("setup, reply, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
async def test_check_reply_cases(test_db, setup, reply, expected):
    facts = await _reply_facts(test_db, setup)
    result = check_reply(reply, facts)
    assert result.codes == expected
    assert result.text == reply


async def test_plan_missing_names_the_act_still_to_tell(test_db):
    facts = await _reply_facts(test_db, TAKEN15_OPTIONS)
    result = check_reply("Dạ 3 giờ chiều mai có khách rồi ạ, chị lấy 1 giờ hay 2 giờ chiều nhé?", facts)
    offer = next(act for act in facts.plan.acts if act.kind == "offer")
    assert result.problems == [f"the reply must still tell: {offer.sentence}"]


async def test_wrong_service_when_a_shop_service_is_not_of_this_turn(test_db):
    """(a): tên một dịch vụ của tiệm mà lượt không có, dù không đi với động từ
    giữ / đặt / dời."""
    facts = await _reply_facts(test_db, dict(turn_facts=[TimeFact(role="free", moment=M15)]))
    result = check_reply("Dạ 3 giờ chiều mai còn trống, gội sấy bên em làm nhanh lắm ạ.", facts)
    assert result.codes == ["wrong_service"]


async def test_turn_service_name_inside_a_longer_name_is_not_another_service(test_db):
    """"Cắt tóc nữ" của lượt không bị đọc thành một dịch vụ khác có tên ngắn
    hơn nằm trong nó."""
    await ServiceCatalogService(test_db).upsert_many([
        Service(id="toc-cat", category="toc", name="Cắt tóc", price_min=80000, price_max=100000,
                duration_minutes=30)])
    facts = await _reply_facts(test_db, PROPOSAL_CUT14)
    result = check_reply("Dạ em giữ chỗ cắt tóc nữ 2 giờ chiều mai cho chị Mai, giá khoảng "
                         "120 nghìn, chị đồng ý không ạ?", facts)
    assert result.codes == []


async def test_unknown_price_outside_every_range(test_db):
    facts = await _reply_facts(test_db, TAKEN15_OPTIONS_PLUS_GEL_PRICE)
    result = check_reply(
        "Dạ 3 giờ chiều mai có khách rồi ạ. Chị lấy 1 giờ, 2 giờ hay 5 giờ chiều mai nhé? "
        "Sơn gel giá 150 nghìn, gói kèm thì 300 nghìn ạ.", facts)
    assert result.codes == ["unknown_price"]


async def test_check_reply_strips_tool_text_first(test_db):
    facts = await _reply_facts(test_db, NO_FACTS)
    result = check_reply("Dạ em chào chị Lan ạ. decide_pending", facts)
    assert (result.text, result.codes) == ("Dạ em chào chị Lan ạ.", [])


# Các lượt của tests/salon/guard/test_plan.py (ngoài PORTED_ROWS).
_M15_FULL = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
_M16_FULL = at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")
_FAR_9 = Moment(iso=datetime(2026, 11, 12, 9, 0, tzinfo=TZ).isoformat(),
                spoken="Thứ Năm 12/11, 9 giờ sáng")
PLAN_SETUPS = [
    dict(turn_facts=[TimeFact(role="free", moment=at(13)),
                     ProposalFact(proposal_id="p-2", proposal_kind="book", at=at(14),
                                  service_ids=[CAT.id])]),
    dict(turn_facts=[ProposalFact(proposal_id="p-2", proposal_kind="book", at=at(14),
                                  service_ids=[GEL.id, GOI.id])]),
    dict(snapshot=dict(unannounced=r0(at(10), outcome="failed")),
         turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=_M15_FULL)]),
    dict(pending_outcome=PendingOutcome(outcome="approved", proposal_id="p-1", kind="book",
                                        at=_M15_FULL, service_ids=[CAT.id]),
         turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=_M15_FULL)]),
    dict(pending_outcome=PendingOutcome(outcome="approved", proposal_id="p-1", kind="move",
                                        at=_M15_FULL, service_ids=[CAT.id]),
         turn_facts=[DecisionFact(outcome="approved", proposal_kind="move", at=_M15_FULL)]),
    dict(snapshot=dict(unannounced=r0(_M15_FULL))),
    dict(snapshot=dict(unannounced=UnannouncedDecision(turn_id=TURN_ID, outcome="approved",
                                                       proposal_kind="book", at=_M15_FULL)),
         turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=_M15_FULL)]),
    dict(snapshot=dict(unannounced=UnannouncedDecision(turn_id=TURN_ID, outcome="failed",
                                                       proposal_kind="book", at=_M15_FULL)),
         turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=_M15_FULL)]),
    dict(snapshot=dict(pending=pending_at(_M15_FULL)),
         turn_facts=[DecisionFact(outcome="clarify", proposal_kind="book", at=_M15_FULL)]),
    dict(turn_facts=[TimeFact(role="free", moment=at(hour)) for hour in (9, 10, 11, 13, 14)]),
    dict(pending_outcome=PendingOutcome(outcome="failed", proposal_id="p-1", kind="book",
                                        at=_M15_FULL, nearest=[at(14), _M16_FULL])),
    dict(turn_facts=[
        ServiceFact(service_id=GEL.id, name=GEL.name, price_min=GEL.price_min,
                    price_max=GEL.price_max, duration_minutes=GEL.duration_minutes, identified=True),
        ServiceFact(service_id=GOI.id, name=GOI.name, price_min=GOI.price_min,
                    price_max=GOI.price_max, duration_minutes=GOI.duration_minutes),
        TimeFact(role="appointment", moment=at(15)), TimeFact(role="appointment", moment=at(17)),
        TimeFact(role="shop_open", moment=at(8)), TimeFact(role="shop_close", moment=at(19)),
        TimeFact(role="busy_until", moment=at(11, 30))]),
    dict(turn_kind="button", turn_facts=[
        DecisionFact(outcome="approved", proposal_kind="book", at=_M15_FULL),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=_M16_FULL, service_ids=[])]),
    dict(turn_facts=[DecisionFact(outcome="rejected", proposal_kind="book", at=_M15_FULL)]),
    dict(turn_facts=[DecisionFact(outcome="rejected", proposal_kind="cancel", at=_M15_FULL)]),
    dict(turn_facts=[TimeFact(role="taken", moment=_FAR_9)]),
    dict(turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=_M15_FULL),
                     ProposalFact(proposal_id="p-2", proposal_kind="book", at=_M16_FULL,
                                  service_ids=[])]),
    dict(turn_facts=[ProposalFact(proposal_id="p-2", proposal_kind="cancel", at=_M15_FULL,
                                  service_ids=[])]),
]
ALL_SETUPS = [*(case[1] for case in CASES), *PLAN_SETUPS, *(row.values[0] for row in PORTED_ROWS)]


@pytest.mark.parametrize("setup", ALL_SETUPS)
async def test_every_plan_template_passes_the_checks(test_db, setup):
    facts = await _reply_facts(test_db, setup)
    text = facts.plan.text()
    if not text:
        return
    result = check_reply(text, facts)
    assert not set(result.codes) & MEANING_CODES, (text, result.codes, result.problems)


async def test_accepted_reply_carries_the_plan_announce_ids(test_db):
    facts = await _reply_facts(test_db, dict(snapshot=dict(unannounced=r0(M15))))
    assert facts.plan.announce_turn_ids() == ["turn-0"]
    accepted = check_reply("Dạ lịch 3 giờ chiều mai em đã đặt xong cho chị Lan rồi ạ.", facts)
    assert (accepted.codes, accepted.announce_turn_ids) == ([], ["turn-0"])
    rejected = check_reply("Dạ em chào chị Lan ạ.", facts)
    assert (rejected.codes, rejected.announce_turn_ids) == (["plan_missing"], [])


async def test_reply_facts_carry_the_plan_and_the_shop_service_names(test_db):
    facts = await _reply_facts(test_db, TAKEN15_OPTIONS)
    assert [act.kind for act in facts.plan.acts] == ["taken", "offer"]
    assert facts.fallback == facts.plan.text()
    assert set(facts.shop_service_names) == {GEL.name, GOI.name, CAT.name}
