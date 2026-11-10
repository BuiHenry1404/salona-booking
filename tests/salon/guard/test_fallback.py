"""TDD cho `guard/fallback.py` (Task 12, spec §7-8): câu mẫu code khi guard
từ chối hai lần / lượt lỗi (`fallback_reply`) và câu báo của lượt nút bấm
(`button_reply`). Số điện thoại tiệm CHỈ xuất hiện khi ghi lịch thất bại.
"""
from __future__ import annotations

import pytest

from app.agents.salon.guard.fallback import (
    BUTTON_STALE_REPLY,
    GENERIC_FALLBACK,
    button_reply,
    fallback_reply,
)
from app.agents.salon.shared.schemas import DecisionFact, PendingOutcome, ProposalFact, TimeFact
from app.core.config import settings
from tests.salon.guard import (
    PHONE,
    TURN_ID,
    a_session,
    a_snapshot,
    at,
    facts_for,
    pending_at,
    r0,
)

M15 = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
ADDRESS = "chị Lan"


@pytest.fixture(autouse=True)
def shop_phone(monkeypatch):
    monkeypatch.setattr(settings, "shop_phone", PHONE)


def _outcome(outcome: str, *, kind: str = "book", nearest=()) -> PendingOutcome:
    return PendingOutcome(outcome=outcome, proposal_id="p-1", kind=kind, at=M15,
                          nearest=list(nearest))


BUTTON_ROWS = [
    pytest.param(_outcome("approved"), "Dạ em đã đặt xong Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.",
                 id="approved_book"),
    pytest.param(_outcome("approved", kind="move"),
                 "Dạ em đã dời lịch sang Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.", id="approved_move"),
    pytest.param(_outcome("approved", kind="cancel"),
                 "Dạ em đã huỷ lịch Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.", id="approved_cancel"),
    pytest.param(_outcome("rejected"),
                 "Dạ vâng, em bỏ lịch Thứ Ba 10/11, 3 giờ chiều rồi ạ. "
                 "Chị Lan muốn giờ khác thì nhắn em nhé.", id="rejected"),
    # Từ chối một đề xuất HUỶ = lịch vẫn còn; "em bỏ lịch …" sẽ làm khách
    # tưởng lịch đã mất.
    pytest.param(_outcome("rejected", kind="cancel"),
                 "Dạ vâng, em giữ nguyên lịch Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.",
                 id="rejected_cancel"),
    pytest.param(_outcome("failed", nearest=[at(14, spoken="2 giờ chiều"), at(16, spoken="4 giờ chiều")]),
                 "Dạ Thứ Ba 10/11, 3 giờ chiều vừa có người đặt mất rồi ạ. "
                 "Còn trống 2 giờ chiều hoặc 4 giờ chiều, chị Lan nhắn em giờ muốn đặt nhé.",
                 id="failed_with_nearest"),
    pytest.param(_outcome("failed"),
                 f"Chị Lan ơi, Thứ Ba 10/11, 3 giờ chiều em chưa ghi được, chị Lan gọi giúp em số "
                 f"{PHONE} nhé ạ.", id="failed_without_nearest"),
    pytest.param(_outcome("not_approvable"),
                 f"Chị Lan ơi, Thứ Ba 10/11, 3 giờ chiều em chưa ghi được, chị Lan gọi giúp em số "
                 f"{PHONE} nhé ạ.", id="not_approvable"),
]


@pytest.mark.parametrize("outcome, expected", BUTTON_ROWS)
def test_button_reply_table(outcome, expected):
    reply = button_reply(outcome, address=ADDRESS, shop_phone=PHONE)
    assert reply == expected
    has_phone = PHONE in reply
    assert has_phone is (outcome.outcome in ("not_approvable", "failed") and not outcome.nearest)


def test_button_reply_without_phone_configured_never_invents_one():
    reply = button_reply(_outcome("failed"), address=ADDRESS, shop_phone="")
    assert "số" not in reply and "Thứ Ba 10/11, 3 giờ chiều" in reply


def test_button_stale_reply_is_a_template():
    assert BUTTON_STALE_REPLY.format(address="anh chị") == (
        "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé.")


FALLBACK_ROWS = [
    pytest.param(
        dict(turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15)],
             pending_decided=True),
        f"Chị Lan ơi, Thứ Ba 10/11, 3 giờ chiều em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ.",
        id="failed"),
    pytest.param(
        dict(turn_facts=[DecisionFact(outcome="approved", proposal_kind="move", at=M15)],
             pending_decided=True),
        "Dạ em đã dời lịch sang Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.", id="approved"),
    pytest.param(
        dict(turn_facts=[ProposalFact(proposal_id="p-2", proposal_kind="book", at=M15,
                                      service_ids=[])]),
        "Dạ em giữ chỗ Thứ Ba 10/11, 3 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?",
        id="new_proposal"),
    pytest.param(
        dict(snapshot=dict(pending=pending_at(M15)), pending_decided=True,
             turn_facts=[DecisionFact(outcome="kept", proposal_kind="book", at=M15)]),
        "Dạ em giữ chỗ Thứ Ba 10/11, 3 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?",
        id="pending"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15))),
        "Dạ em đã đặt xong Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ.", id="r0"),
    pytest.param(
        dict(snapshot=dict(unannounced=r0(M15, outcome="failed"))),
        f"Chị Lan ơi, Thứ Ba 10/11, 3 giờ chiều em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ.",
        id="r0_failed"),
    # Thất bại của lượt VÀ R0 thất bại: nói cả hai (giờ R0 nằm trong must_mention).
    pytest.param(
        dict(snapshot=dict(unannounced=r0(at(10, spoken="Thứ Ba 10/11, 10 giờ sáng"),
                                          outcome="failed")),
             turn_facts=[DecisionFact(outcome="failed", proposal_kind="book", at=M15)],
             pending_decided=True),
        f"Chị Lan ơi, Thứ Ba 10/11, 3 giờ chiều em chưa ghi được, chị Lan gọi giúp em số {PHONE} nhé ạ. "
        "Dạ lịch Thứ Ba 10/11, 10 giờ sáng em cũng chưa ghi được ạ.",
        id="failed_and_r0_failed"),
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="day", opened_turn_id=TURN_ID)})),
        "Dạ chị Lan muốn ngày nào ạ?", id="opened_session"),
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="day", opened_turn_id="turn-0")})),
        "Dạ chị Lan nói lại giúp em được không ạ?", id="old_session_is_generic"),
    pytest.param(dict(), "Dạ chị Lan nói lại giúp em được không ạ?", id="generic"),
    # F3 (owner 11/11): a taken time found this turn is announced first.
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="time", opened_turn_id=TURN_ID)}),
             turn_facts=[TimeFact(role="taken", moment=M15)]),
        "Dạ Thứ Ba 10/11, 3 giờ chiều đã có khách rồi ạ. Dạ chị Lan muốn mấy giờ ạ?",
        id="taken_then_session_question"),
    # Final review 11/11: the fallback of a just-opened time session names its free times.
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(
                 field="time", opened_turn_id=TURN_ID,
                 options=[at(13, spoken="Thứ Ba 10/11, 1 giờ chiều"),
                          at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")])}),
             turn_facts=[TimeFact(role="taken", moment=M15)]),
        "Dạ Thứ Ba 10/11, 3 giờ chiều đã có khách rồi ạ. Dạ còn trống Thứ Ba 10/11, 1 giờ chiều "
        "hoặc Thứ Ba 10/11, 4 giờ chiều, chị Lan chọn giờ nào ạ?",
        id="taken_then_session_options"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="taken", moment=M15),
                         ProposalFact(proposal_id="p-2", proposal_kind="book",
                                      at=at(16, spoken="Thứ Ba 10/11, 4 giờ chiều"), service_ids=[])]),
        "Dạ Thứ Ba 10/11, 3 giờ chiều đã có khách rồi ạ. "
        "Dạ em giữ chỗ Thứ Ba 10/11, 4 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?",
        id="taken_then_proposal"),
]


@pytest.mark.parametrize("setup, expected", FALLBACK_ROWS)
async def test_fallback_has_phone_only_when_failed(test_db, setup, expected):
    setup = dict(setup)
    snapshot = a_snapshot(**setup.pop("snapshot", {}))
    facts = await facts_for(test_db, snapshot=snapshot, **setup)
    reply = fallback_reply(facts)
    assert reply == expected
    assert (PHONE in reply) is (reply.startswith("Chị Lan ơi"))


async def test_fallback_after_approve_also_asks_the_next_proposal(test_db):
    """Duyệt xong VÀ có đề xuất mới trong cùng lượt: câu mẫu báo xong rồi hỏi
    đề xuất mới — không để nút Đồng ý/Không hiện ra mà không có câu hỏi."""
    m16 = at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")
    facts = await facts_for(test_db, pending_decided=True, turn_facts=[
        DecisionFact(outcome="approved", proposal_kind="book", at=M15),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=m16, service_ids=[]),
    ])
    assert fallback_reply(facts) == (
        "Dạ em đã đặt xong Thứ Ba 10/11, 3 giờ chiều cho chị Lan ạ. "
        "Dạ em giữ chỗ Thứ Ba 10/11, 4 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?")


async def test_fallback_on_button_turn_skips_the_decision_already_reported(test_db):
    m16 = at(16, spoken="Thứ Ba 10/11, 4 giờ chiều")
    facts = await facts_for(test_db, turn_kind="button", pending_decided=True, turn_facts=[
        DecisionFact(outcome="approved", proposal_kind="book", at=M15),
        ProposalFact(proposal_id="p-2", proposal_kind="book", at=m16, service_ids=[]),
    ])
    assert fallback_reply(facts) == (
        "Dạ em giữ chỗ Thứ Ba 10/11, 4 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?")


async def test_fallback_for_a_cancel_proposal_does_not_say_hold(test_db):
    facts = await facts_for(test_db, turn_facts=[
        ProposalFact(proposal_id="p-2", proposal_kind="cancel", at=M15, service_ids=[]),
    ])
    reply = fallback_reply(facts)
    assert "giữ chỗ" not in reply and "huỷ" in reply and reply.endswith("?")


async def test_fallback_without_phone_configured_never_invents_one(test_db, monkeypatch):
    monkeypatch.setattr(settings, "shop_phone", None)
    facts = await facts_for(test_db, pending_decided=True, turn_facts=[
        DecisionFact(outcome="failed", proposal_kind="book", at=M15)])
    reply = fallback_reply(facts)
    assert "None" not in reply and "gọi" not in reply and "Thứ Ba 10/11, 3 giờ chiều" in reply


def test_generic_fallback_template():
    assert GENERIC_FALLBACK.format(address="anh chị") == "Dạ anh chị nói lại giúp em được không ạ?"
