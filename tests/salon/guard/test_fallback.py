"""TDD cho `guard/fallback.py` (Task 12, spec §7-8): câu báo của lượt nút bấm
(`button_reply`) và các câu mẫu cố định. Số điện thoại tiệm CHỈ xuất hiện khi
ghi lịch thất bại. `{at}` là nhãn giờ ngắn ("3 giờ chiều mai"; ngày khác giữ
`spoken`). Câu dự phòng của lượt gõ chữ (câu của kế hoạch) ở test_plan.py.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.agents.salon.guard.fallback import (
    BUTTON_STALE_REPLY,
    FAILED_NEAREST_LEAD,
    FAILED_NEAREST_REPLY,
    GENERIC_FALLBACK,
    NEAREST_OFFER,
    _done,
    button_reply,
)
from app.agents.salon.shared.schemas import Moment, PendingOutcome
from app.core.clock import TZ
from app.core.config import settings
from tests.salon.guard import PHONE, at

M15 = at(15, spoken="Thứ Ba 10/11, 3 giờ chiều")
ADDRESS = "chị Lan"
TODAY = date(2026, 11, 9)


@pytest.fixture(autouse=True)
def shop_phone(monkeypatch):
    monkeypatch.setattr(settings, "shop_phone", PHONE)


def _outcome(outcome: str, *, kind: str = "book", nearest=()) -> PendingOutcome:
    return PendingOutcome(outcome=outcome, proposal_id="p-1", kind=kind, at=M15,
                          nearest=list(nearest))


BUTTON_ROWS = [
    pytest.param(_outcome("approved"), "Dạ em đã đặt xong 3 giờ chiều mai cho chị Lan ạ.",
                 id="approved_book"),
    pytest.param(_outcome("approved", kind="move"),
                 "Dạ em đã dời lịch sang 3 giờ chiều mai cho chị Lan ạ.", id="approved_move"),
    pytest.param(_outcome("approved", kind="cancel"),
                 "Dạ em đã huỷ lịch 3 giờ chiều mai cho chị Lan ạ.", id="approved_cancel"),
    pytest.param(_outcome("rejected"),
                 "Dạ vâng, em bỏ lịch 3 giờ chiều mai rồi ạ. "
                 "Chị Lan muốn giờ khác thì nhắn em nhé.", id="rejected"),
    # Từ chối một đề xuất HUỶ = lịch vẫn còn; "em bỏ lịch …" sẽ làm khách
    # tưởng lịch đã mất.
    pytest.param(_outcome("rejected", kind="cancel"),
                 "Dạ vâng, em giữ nguyên lịch 3 giờ chiều mai cho chị Lan ạ.",
                 id="rejected_cancel"),
    pytest.param(_outcome("failed", nearest=[at(14), at(16)]),
                 "Dạ 3 giờ chiều mai vừa có người đặt mất rồi ạ. "
                 "Còn trống 2 giờ chiều mai hoặc 4 giờ chiều mai, chị Lan nhắn em giờ muốn đặt nhé.",
                 id="failed_with_nearest"),
    pytest.param(_outcome("failed"),
                 f"Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em số "
                 f"{PHONE} nhé ạ.", id="failed_without_nearest"),
    pytest.param(_outcome("not_approvable"),
                 f"Chị Lan ơi, 3 giờ chiều mai em chưa ghi được, chị Lan gọi giúp em số "
                 f"{PHONE} nhé ạ.", id="not_approvable"),
]


@pytest.mark.parametrize("outcome, expected", BUTTON_ROWS)
def test_button_reply_table(outcome, expected):
    reply = button_reply(outcome, address=ADDRESS, shop_phone=PHONE, today=TODAY)
    assert reply == expected
    has_phone = PHONE in reply
    assert has_phone is (outcome.outcome in ("not_approvable", "failed") and not outcome.nearest)


def test_button_reply_without_phone_configured_never_invents_one():
    reply = button_reply(_outcome("failed"), address=ADDRESS, shop_phone="", today=TODAY)
    assert "số" not in reply and "3 giờ chiều mai" in reply


def test_button_reply_keeps_the_spoken_label_for_other_days():
    far = Moment(iso=datetime(2026, 11, 12, 9, 0, tzinfo=TZ).isoformat(),
                 spoken="Thứ Năm 12/11, 9 giờ sáng")
    outcome = PendingOutcome(outcome="approved", proposal_id="p-1", kind="book", at=far)
    assert button_reply(outcome, address=ADDRESS, shop_phone=PHONE, today=TODAY) == (
        "Dạ em đã đặt xong Thứ Năm 12/11, 9 giờ sáng cho chị Lan ạ.")


def test_done_move_names_services_before_the_new_time():
    assert _done("move", "3 giờ chiều mai", address=ADDRESS, service_names=["Cắt tóc nữ"]) == (
        "Dạ em đã dời lịch cắt tóc nữ sang 3 giờ chiều mai cho chị Lan ạ.")


def test_done_move_without_services_keeps_the_old_wording():
    assert _done("move", "3 giờ chiều mai", address=ADDRESS) == (
        "Dạ em đã dời lịch sang 3 giờ chiều mai cho chị Lan ạ.")


def test_done_book_with_services_names_them_at_the_time():
    assert _done("book", "3 giờ chiều mai", address=ADDRESS,
                 service_names=["Cắt tóc nữ", "Gội sấy"]) == (
        "Dạ em đã đặt xong cắt tóc nữ và gội sấy lúc 3 giờ chiều mai cho chị Lan ạ.")


def test_failed_nearest_reply_is_its_lead_then_the_offer():
    assert FAILED_NEAREST_REPLY == f"{FAILED_NEAREST_LEAD} {NEAREST_OFFER}"


def test_button_stale_reply_is_a_template():
    assert BUTTON_STALE_REPLY.format(address="anh chị") == (
        "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé.")


def test_generic_fallback_template():
    assert GENERIC_FALLBACK.format(address="anh chị") == "Dạ anh chị nói lại giúp em được không ạ?"
