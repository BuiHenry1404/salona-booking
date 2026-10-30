"""Factory dùng chung cho test của `app/agents/booking/` (M2–M5 tái dùng).

`make_ctx` dựng một `TurnContext` hợp lệ với giá trị mặc định thực tế — ngày
giờ luôn tính TƯƠNG ĐỐI theo "bây giờ" (không hard-code một ngày lịch), để
test không mục nát theo thời gian.
"""
from __future__ import annotations

from datetime import timedelta

from app.agents.booking.context import CustomerRef, TurnContext
from app.core.clock import now_utc, to_local
from app.core.vi_format import _WEEKDAYS
from app.models.conversation import GoalProgress
from app.models.shop import ShopHours, ShopStatusView


def _calendar_14d(today) -> list[tuple]:
    items = []
    for offset in range(14):
        d = today + timedelta(days=offset)
        items.append((d, f"{_WEEKDAYS[d.weekday()]} {d.day}/{d.month}"))
    return items


def make_ctx(**overrides) -> TurnContext:
    now_local = to_local(now_utc())
    today = now_local.date()

    defaults = dict(
        turn_id="t1",
        asked_at=now_local,
        now_local=now_local,
        today=today,
        calendar_14d=_calendar_14d(today),
        customer=CustomerRef(
            id="507f1f77bcf86cd799439011",
            display_name="Nguyễn Thị Lan",
            address_phrase="chị Lan",
            staff_note=None,
        ),
        shop_status=ShopStatusView(is_busy=False),
        shop_hours=ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
        upcoming=[],
        recent_visits=[],
        pending=None,
        recently_declined=None,
        unannounced=None,
        queued_requests=[],
        open_question=None,
        answers_open_question_hint=False,
        last_bot_text="",
        previous_turns=[],
        goal=GoalProgress(),
        slots=None,
        digest_text="",
    )
    defaults.update(overrides)
    return TurnContext(**defaults)
