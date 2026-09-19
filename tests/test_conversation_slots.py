"""sanitize_slots: LLM sinh slots, code lọc lại. Không chạm DB, không chạm LLM.

Đừng nhầm với tests/test_slots.py — file đó nói về KHUNG GIỜ (app/core/slots.py).
"""
from datetime import datetime, timezone

import structlog

from app.models.conversation import ConversationSlots
from app.services.conversation_state import sanitize_slots

# 2026-09-19 10:00 UTC = 17:00 giờ VN (UTC+7).
NOW = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
IDS = {"aaaaaaaaaaaaaaaaaaaaaaaa"}


def _clean(**kwargs):
    return sanitize_slots(ConversationSlots(**kwargs), now=NOW, valid_ids=IDS)


def test_keeps_a_fully_valid_set():
    slots = _clean(intent="book", service="làm móng bột", day="2026-09-20",
                   time="15:00", target_appointment_id="aaaaaaaaaaaaaaaaaaaaaaaa")
    assert slots.intent == "book"
    assert slots.day == "2026-09-20"
    assert slots.time == "15:00"
    assert slots.target_appointment_id == "aaaaaaaaaaaaaaaaaaaaaaaa"


def test_unknown_intent_is_dropped():
    assert _clean(intent="booking", service="làm tóc").intent is None


def test_a_day_in_the_past_is_dropped():
    assert _clean(day="2026-09-18", service="làm tóc").day is None


def test_today_is_not_the_past():
    assert _clean(day="2026-09-19", service="làm tóc").day == "2026-09-19"


def test_a_time_already_gone_today_is_dropped():
    """17:00 giờ VN rồi thì '9 giờ sáng hôm nay' không còn là thứ đang nhắm."""
    assert _clean(day="2026-09-19", time="09:00").time is None


def test_a_time_already_gone_survives_on_a_later_day():
    assert _clean(day="2026-09-20", time="09:00").time == "09:00"


def test_an_unparseable_day_or_time_is_dropped():
    slots = _clean(day="thứ Năm tuần sau", time="25:99", service="làm tóc")
    assert slots.day is None and slots.time is None


def test_an_appointment_id_the_customer_does_not_own_is_dropped():
    """Model từng bịa id (BUG-2). Đối chiếu DB, không tin chuỗi model gõ."""
    slots = _clean(intent="cancel", target_appointment_id="bbbbbbbbbbbbbbbbbbbbbbbb")
    assert slots.target_appointment_id is None
    assert slots.intent == "cancel"      # field khác không bị vạ lây


def test_service_is_capped_and_flattened():
    slots = _clean(service="làm  tóc\nnhuộm " + "x" * 200)
    assert len(slots.service) <= 80
    assert "\n" not in slots.service


def test_declined_keeps_the_last_six_parseable_entries():
    raw = [f"2026-09-2{i}T09:00:00+07:00" for i in range(8)]
    slots = _clean(declined=[*raw, "không phải giờ"])
    assert len(slots.declined) == 6
    assert slots.declined[-1] == raw[-1]


def test_everything_empty_becomes_none():
    assert _clean() is None
    assert sanitize_slots(None, now=NOW, valid_ids=IDS) is None


def test_each_dropped_field_is_logged():
    """Không có log này thì slot sai âm thầm biến mất — đúng lối fail-soft đã
    che ba lỗi nặng nhất của dự án.

    Dùng `structlog.testing.capture_logs()` chứ không phải `caplog`: app cấu
    hình structlog với `WriteLoggerFactory` (ghi thẳng stdout, không qua
    module `logging` chuẩn của Python), nên `caplog` không bắt được gì.
    """
    with structlog.testing.capture_logs() as logs:
        _clean(intent="booking", day="2026-01-01")
    dropped = [e for e in logs if e["event"] == "state_slot_dropped"]
    assert {e["extra"]["field"] for e in dropped} == {"intent", "day"}
