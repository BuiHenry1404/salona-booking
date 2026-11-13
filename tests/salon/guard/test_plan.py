"""TDD cho `guard/plan.py` (spec plan-guided reply §1, "Quyết định sau builder
check" mục 2-4): code quyết định câu trả lời phải nói gì — một `ReplyPlan` gồm
các hành động, mỗi hành động có câu mẫu tiếng Việt (nhãn giờ ngắn) cùng giờ /
giá / dịch vụ câu đó nêu. Câu mẫu của kế hoạch cũng là câu dự phòng
(`ReplyFacts.fallback`).

Các ca cũ của `compose_fallback_asking` (tests/salon/guard/test_fallback.py)
chuyển sang đây, cùng kỳ vọng, `{at}` giờ là nhãn ngắn ("3 giờ chiều mai").
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

import pytest

from app.agents.salon.guard.fallback import GENERIC_FALLBACK, fallback_reply
from app.agents.salon.guard.plan import PLAN_HEADER, PlanAct, ReplyPlan, build_plan
from app.agents.salon.shared.context import TurnSnapshot
from app.agents.salon.shared.schemas import (
    DecisionFact,
    Moment,
    PendingOutcome,
    ProposalFact,
    ServiceFact,
    TimeFact,
)
from app.core.clock import TZ
from app.core.config import settings
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService
from tests.salon.guard import (
    PHONE,
    TURN_ID,
    a_session,
    a_snapshot,
    at,
    catalog_for,
    facts_for,
    pending_at,
    r0,
    seed_catalog,
)

M15 = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
M16 = at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")
CAT = Service(id="toc-cat-nu", category="toc", name="Cắt tóc nữ", price_min=100000,
              price_max=150000, duration_minutes=45)


@pytest.fixture(autouse=True)
def shop_phone(monkeypatch):
    monkeypatch.setattr(settings, "shop_phone", PHONE)


async def plan_for(db, *, snapshot: Optional[TurnSnapshot] = None, turn_facts: Sequence = (),
                   pending_outcome: Optional[PendingOutcome] = None, turn_kind: str = "typed",
                   shop_phone: Optional[str] = PHONE) -> ReplyPlan:
    snapshot = snapshot or a_snapshot()
    catalog = await catalog_for(db, snapshot=snapshot, turn_facts=turn_facts,
                                pending_outcome=pending_outcome)
    return build_plan(catalog, snapshot=snapshot, turn_facts=list(turn_facts),
                      pending_outcome=pending_outcome, turn_id=TURN_ID, turn_kind=turn_kind,
                      shop_phone=shop_phone)


def _kinds(plan: ReplyPlan) -> list[str]:
    return [act.kind for act in plan.acts]


# ---------------------------------------------------------------------------
# Thứ tự và loại trừ giữa các hành động
# ---------------------------------------------------------------------------


async def test_taken_then_session_options(test_db):
    session = a_session(field="time", opened_turn_id=TURN_ID, options=[at(13), at(14), at(17)])
    plan = await plan_for(test_db, snapshot=a_snapshot(sessions={"appointment": session}),
                          turn_facts=[TimeFact(role="taken", moment=at(15))])
    assert _kinds(plan) == ["taken", "offer"]
    assert plan.text() == (
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ còn trống 1 giờ chiều mai, "
        "2 giờ chiều mai hoặc 5 giờ chiều mai, chị Lan chọn giờ nào ạ?")
    assert plan.asks_session == "appointment"
    assert plan.taken_clocks() == {(15, 0)}
    assert plan.offered_clocks() == {(13, 0), (14, 0), (17, 0)}
    assert plan.clocks() == {(15, 0), (13, 0), (14, 0), (17, 0)}


async def test_new_proposal_names_its_service_and_suppresses_offers(test_db):
    await ServiceCatalogService(test_db).upsert_many([CAT])
    plan = await plan_for(test_db, turn_facts=[
        TimeFact(role="free", moment=at(13)),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=at(14), service_ids=["toc-cat-nu"]),
    ])
    assert _kinds(plan) == ["proposal", "price"]
    proposal, price = plan.acts
    assert proposal.sentence == (
        "Dạ em giữ chỗ cắt tóc nữ lúc 2 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?")
    assert proposal.service_names == ["Cắt tóc nữ"]
    assert proposal.clocks == [(14, 0)]
    assert price.sentence == "Dạ cắt tóc nữ giá 100.000đ-150.000đ ạ."
    assert price.prices == [(100000, 150000)]
    assert plan.prices() == [(100000, 150000)]
    assert plan.offered_clocks() == {(14, 0)}
    assert plan.asks_session is None


async def test_proposal_with_two_services_joins_their_names(test_db):
    await seed_catalog(test_db)
    plan = await plan_for(test_db, turn_facts=[
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=at(14),
                     service_ids=["nail-gel", "toc-goi-say"]),
    ])
    assert plan.acts[0].sentence == (
        "Dạ em giữ chỗ sơn gel và gội sấy lúc 2 giờ chiều mai cho chị Lan, "
        "chị Lan đồng ý không ạ?")
    assert _kinds(plan) == ["proposal", "price", "price"]


async def test_failed_then_done_order_and_announce_ids(test_db):
    plan = await plan_for(
        test_db, snapshot=a_snapshot(unannounced=r0(at(10), outcome="failed")),
        turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)])
    assert _kinds(plan) == ["failed", "done"]
    assert plan.announce_turn_ids() == ["turn-0", TURN_ID]
    assert plan.text() == (
        f"Chị Lan ơi, 10 giờ sáng mai em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ. "
        "Dạ em đã đặt xong 3 giờ chiều mai cho chị Lan ạ.")


async def test_done_names_the_service_of_the_pending_it_approved(test_db):
    await ServiceCatalogService(test_db).upsert_many([CAT])
    plan = await plan_for(
        test_db, snapshot=a_snapshot(pending=pending_at(M15, service_ids=["toc-cat-nu"])),
        turn_facts=[DecisionFact(outcome="approved", proposal_kind="book", at=M15)])
    assert _kinds(plan) == ["done"]
    assert plan.acts[0].sentence == "Dạ em đã đặt xong cắt tóc nữ lúc 3 giờ chiều mai cho chị Lan ạ."
    assert plan.acts[0].service_names == ["Cắt tóc nữ"]


async def test_kept_pending_is_not_re_asked(test_db):
    plan = await plan_for(
        test_db, snapshot=a_snapshot(pending=pending_at(M15)),
        turn_facts=[DecisionFact(outcome="kept", proposal_kind="book", at=M15)])
    assert "proposal" not in _kinds(plan)
    assert plan.acts == []


async def test_pending_without_a_decision_this_turn_is_not_re_asked(test_db):
    """Chỉ `clarify` hỏi lại đề xuất chờ (brief Task 1 bước 5). Lượt gõ chữ
    chưa `decide_pending` thì guard bắt viết lại có tool (spec quyết định 6);
    câu mẫu không tự hỏi lại."""
    plan = await plan_for(test_db, snapshot=a_snapshot(pending=pending_at(M15)))
    assert plan.acts == []


async def test_clarify_pending_is_re_asked(test_db):
    plan = await plan_for(
        test_db, snapshot=a_snapshot(pending=pending_at(M15)),
        turn_facts=[DecisionFact(outcome="clarify", proposal_kind="book", at=M15)])
    assert _kinds(plan) == ["proposal"]
    assert plan.text() == "Dạ em giữ chỗ 3 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?"


async def test_free_offers_capped_at_three(test_db):
    plan = await plan_for(test_db, turn_facts=[
        TimeFact(role="free", moment=at(hour)) for hour in (9, 10, 11, 13, 14)])
    assert _kinds(plan) == ["offer"]
    assert plan.acts[0].clocks == [(9, 0), (10, 0), (11, 0)]
    assert plan.text() == (
        "Dạ còn trống 9 giờ sáng mai, 10 giờ sáng mai hoặc 11 giờ sáng mai, "
        "chị Lan chọn giờ nào ạ?")
    assert plan.asks_session is None


async def test_nearest_of_a_failed_decision_is_offered(test_db):
    outcome = PendingOutcome(outcome="failed", proposal_id="p-1", kind="book", at=M15,
                             nearest=[at(14), M16])
    plan = await plan_for(test_db, pending_outcome=outcome)
    assert _kinds(plan) == ["failed", "offer"]
    assert plan.acts[1].sentence == (
        "Còn trống 2 giờ chiều mai hoặc 4 giờ chiều mai, chị Lan nhắn em giờ muốn đặt nhé.")
    assert plan.offered_clocks() == {(14, 0), (16, 0)}


async def test_price_appointments_hours_busy_acts(test_db):
    await seed_catalog(test_db)
    gel = ServiceFact(service_id="nail-gel", name="Sơn gel", price_min=120000, price_max=180000,
                      duration_minutes=45, identified=True)
    listed = ServiceFact(service_id="toc-goi-say", name="Gội sấy", price_min=50000,
                         price_max=80000, duration_minutes=30)
    plan = await plan_for(test_db, turn_facts=[
        gel, listed,
        TimeFact(role="appointment", moment=at(15)), TimeFact(role="appointment", moment=at(17)),
        TimeFact(role="shop_open", moment=at(8)), TimeFact(role="shop_close", moment=at(19)),
        TimeFact(role="busy_until", moment=at(11, 30)),
    ])
    assert _kinds(plan) == ["price", "appointments", "hours", "busy"]
    price, appointments, hours, busy = plan.acts
    assert price.sentence == "Dạ sơn gel giá 120.000đ-180.000đ ạ."
    assert price.prices == [(120000, 180000)] and price.service_names == ["Sơn gel"]
    assert appointments.sentence == "Dạ chị Lan có lịch hẹn 3 giờ chiều mai và 5 giờ chiều mai ạ."
    assert appointments.clocks == [(15, 0), (17, 0)]
    assert hours.sentence == "Dạ tiệm mở cửa từ 8 giờ sáng đến 7 giờ tối ạ."
    assert hours.clocks == [(8, 0), (19, 0)]
    assert busy.sentence == "Dạ tiệm đang bận tới 11 giờ rưỡi sáng mai ạ."
    assert busy.clocks == [(11, 30)]
    assert plan.prices() == [(120000, 180000)]
    assert plan.text().startswith("Dạ sơn gel giá 120.000đ-180.000đ ạ. Dạ chị Lan có lịch hẹn")


async def test_button_turn_skips_its_own_decision(test_db):
    plan = await plan_for(test_db, turn_kind="button", turn_facts=[
        DecisionFact(outcome="approved", proposal_kind="book", at=M15),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=M16, service_ids=[]),
    ])
    assert _kinds(plan) == ["proposal"]
    assert plan.text() == "Dạ em giữ chỗ 4 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?"
    assert plan.announce_turn_ids() == []


async def test_rejected_decision_is_released(test_db):
    plan = await plan_for(test_db, turn_facts=[
        DecisionFact(outcome="rejected", proposal_kind="book", at=M15)])
    assert _kinds(plan) == ["released"]
    assert plan.text() == (
        "Dạ vâng, em bỏ lịch 3 giờ chiều mai rồi ạ. Chị Lan muốn giờ khác thì nhắn em nhé.")


async def test_rejected_cancel_keeps_the_appointment(test_db):
    plan = await plan_for(test_db, turn_facts=[
        DecisionFact(outcome="rejected", proposal_kind="cancel", at=M15)])
    assert plan.text() == "Dạ vâng, em giữ nguyên lịch 3 giờ chiều mai cho chị Lan ạ."


async def test_no_acts_means_empty_text_and_render(test_db):
    plan = await plan_for(test_db)
    assert plan.acts == []
    assert plan.text() == "" and plan.render() == ""
    assert plan.clocks() == set() and plan.prices() == [] and plan.announce_turn_ids() == []


def test_render_lists_sentences_under_the_header():
    plan = ReplyPlan(acts=[
        PlanAct(kind="taken", sentence="Dạ 3 giờ chiều mai đã có khách rồi ạ.", clocks=[(15, 0)],
                taken_clocks=[(15, 0)]),
        PlanAct(kind="ask_field", sentence="Dạ chị Lan muốn mấy giờ ạ?"),
    ])
    assert plan.render() == (
        f"{PLAN_HEADER}\n- Dạ 3 giờ chiều mai đã có khách rồi ạ.\n- Dạ chị Lan muốn mấy giờ ạ?")


def test_text_capitalizes_every_sentence():
    plan = ReplyPlan(acts=[PlanAct(kind="released", sentence="dạ vâng. chị Lan nhắn em nhé.")])
    assert plan.text() == "Dạ vâng. Chị Lan nhắn em nhé."


async def test_other_days_keep_the_full_spoken_label(test_db):
    far = Moment(iso=datetime(2026, 11, 12, 9, 0, tzinfo=TZ).isoformat(),
                 spoken="Thứ Năm 12/11, 9 giờ sáng")
    plan = await plan_for(test_db, turn_facts=[TimeFact(role="taken", moment=far)])
    assert plan.text() == "Dạ Thứ Năm 12/11, 9 giờ sáng đã có khách rồi ạ."


# ---------------------------------------------------------------------------
# Ca chuyển từ `compose_fallback_asking` (cùng kỳ vọng, `{at}` là nhãn ngắn)
# ---------------------------------------------------------------------------

PORTED_ROWS = [
    pytest.param(
        dict(turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15)]),
        f"Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ.",
        id="failed"),
    pytest.param(
        dict(turn_facts=[DecisionFact(outcome="approved", proposal_kind="move", at=M15)]),
        "Dạ em đã dời lịch sang 3 giờ chiều mai cho chị Lan ạ.", id="approved"),
    pytest.param(
        dict(turn_facts=[ProposalFact(proposal_id="p-2", proposal_kind="book", at=M15,
                                      service_ids=[])]),
        "Dạ em giữ chỗ 3 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?", id="new_proposal"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15))),
        "Dạ em đã đặt xong 3 giờ chiều mai cho chị Lan ạ.", id="r0"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15, outcome="failed"))),
        f"Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ.",
        id="r0_failed"),
    # Thất bại của lượt VÀ R0 thất bại: nói cả hai, số điện thoại chỉ một lần.
    pytest.param(
        dict(snapshot=dict(unannounced=r0(at(10, spoken="Thứ Ba 10/11, 10 giờ sáng"),
                                          outcome="failed")),
             turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15)]),
        f"Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ. "
        "Dạ lịch 10 giờ sáng mai em cũng chưa ghi được ạ.",
        id="failed_and_r0_failed"),
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="day", opened_turn_id=TURN_ID)})),
        "Dạ chị Lan muốn ngày nào ạ?", id="opened_session"),
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="day", opened_turn_id="turn-0")})),
        "", id="old_session_has_no_act"),
    pytest.param(dict(), "", id="generic"),
    # F3 (owner 11/11): a taken time found this turn is announced first.
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="time", opened_turn_id=TURN_ID)}),
             turn_facts=[TimeFact(role="taken", moment=M15)]),
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ chị Lan muốn mấy giờ ạ?",
        id="taken_then_session_question"),
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(
                 field="time", opened_turn_id=TURN_ID,
                 options=[at(13, spoken="Thứ Ba 10/11, 1 giờ chiều"),
                          at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")])}),
             turn_facts=[TimeFact(role="taken", moment=M15)]),
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ còn trống 1 giờ chiều mai "
        "hoặc 4 giờ chiều mai, chị Lan chọn giờ nào ạ?",
        id="taken_then_session_options"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="taken", moment=M15),
                         ProposalFact(proposal_id="p-2", proposal_kind="book", at=M16,
                                      service_ids=[])]),
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. "
        "Dạ em giữ chỗ 4 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?",
        id="taken_then_proposal"),
]


@pytest.mark.parametrize("setup, expected", PORTED_ROWS)
async def test_plan_text_has_phone_only_when_failed(test_db, setup, expected):
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    plan = await plan_for(test_db, snapshot=snapshot, **setup)
    text = plan.text()
    assert text == expected
    assert (PHONE in text) is text.startswith("Chị Lan ơi")


async def test_plan_after_approve_also_asks_the_next_proposal(test_db):
    """Duyệt xong VÀ có đề xuất mới trong cùng lượt: báo xong rồi hỏi đề xuất
    mới — không để nút Đồng ý/Không hiện ra mà không có câu hỏi."""
    plan = await plan_for(test_db, turn_facts=[
        DecisionFact(outcome="approved", proposal_kind="book", at=M15),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=M16, service_ids=[]),
    ])
    assert _kinds(plan) == ["done", "proposal"]
    assert plan.text() == (
        "Dạ em đã đặt xong 3 giờ chiều mai cho chị Lan ạ. "
        "Dạ em giữ chỗ 4 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?")


async def test_plan_for_a_cancel_proposal_does_not_say_hold(test_db):
    plan = await plan_for(test_db, turn_facts=[
        ProposalFact(proposal_id="p-2", proposal_kind="cancel", at=M15, service_ids=[])])
    text = plan.text()
    assert "giữ chỗ" not in text and "huỷ" in text and text.endswith("?")


async def test_plan_without_phone_configured_never_invents_one(test_db):
    plan = await plan_for(test_db, shop_phone=None, turn_facts=[
        DecisionFact(outcome="failed", proposal_kind="book", at=M15)])
    text = plan.text()
    assert "None" not in text and "gọi" not in text and "3 giờ chiều mai" in text


# ---------------------------------------------------------------------------
# `build_reply_facts` lấy câu dự phòng + phiên được hỏi từ kế hoạch
# ---------------------------------------------------------------------------


async def test_reply_facts_fallback_is_the_plan_text(test_db):
    session = a_session(field="time", opened_turn_id=TURN_ID, options=[at(13), at(14)])
    facts = await facts_for(test_db, snapshot=a_snapshot(sessions={"appointment": session}),
                            turn_facts=[TimeFact(role="taken", moment=M15)])
    assert fallback_reply(facts) == (
        "Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ còn trống 1 giờ chiều mai "
        "hoặc 2 giờ chiều mai, chị Lan chọn giờ nào ạ?")
    assert facts.asked_session == "appointment"


async def test_reply_facts_without_acts_falls_back_to_the_generic_question(test_db):
    facts = await facts_for(test_db)
    assert fallback_reply(facts) == GENERIC_FALLBACK.format(address="chị Lan")
    assert facts.asked_session is None


async def test_reply_facts_uses_the_configured_shop_phone(test_db):
    facts = await facts_for(test_db, pending_decided=True, turn_facts=[
        DecisionFact(outcome="failed", proposal_kind="book", at=M15)])
    assert PHONE in fallback_reply(facts)
