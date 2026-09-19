"""sanitize_slots: LLM sinh slots, code lọc lại. Không chạm DB, không chạm LLM.

Đừng nhầm với tests/test_slots.py — file đó nói về KHUNG GIỜ (app/core/slots.py).
"""
import os
import time as _time
from datetime import datetime, timezone

import pytest
import structlog

from app.agents.booking_graph.context import render_slots
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


def test_a_time_without_a_day_is_kept():
    """Không có ngày thì không suy ra được giờ đó đã qua hay chưa — giữ lại.
    Khách nói 'tầm 9 giờ sáng' trước khi chốt ngày là chuyện bình thường."""
    slots = _clean(time="09:00", service="làm tóc")
    assert slots.time == "09:00"


def test_a_day_too_far_ahead_is_dropped():
    """Không có trần trên thì "9999-12-31" đi qua sạch, mà khối slots không in
    năm — prompt ghi một thứ trong tuần, khối bối cảnh ghi thứ khác."""
    assert _clean(day="9999-12-31", service="làm tóc").day is None
    assert _clean(day="2026-12-31", service="làm tóc").day is None   # +103 ngày


def test_a_day_inside_the_window_survives():
    assert _clean(day="2026-12-17", service="làm tóc").day == "2026-12-17"   # +89 ngày


def test_a_time_is_dropped_together_with_its_day():
    """Model CÓ nói ngày nhưng ngày bị loại: giờ trơ lại sẽ bị đọc thành giờ
    đó HÔM NAY."""
    # service để slots không rỗng hoàn toàn (rỗng thì hàm trả None).
    for bad in ("9999-12-31", "2026-09-18", "thứ Năm tuần sau"):
        slots = _clean(day=bad, time="09:00", service="làm tóc")
        assert slots.day is None and slots.time is None, bad


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


def test_a_naive_declined_is_normalised_to_vietnam_time():
    """Bẫy nặng nhất: naive datetime tới astimezone() thì Python lấy múi giờ
    của TIẾN TRÌNH — đúng trên máy dev (TZ=+07), lệch 7 tiếng trong container
    UTC. sanitize_slots phải CHUẨN HOÁ, không chỉ kiểm parse được."""
    slots = _clean(declined=["2026-09-20T09:00:00"])
    assert slots.declined == ["2026-09-20T09:00:00+07:00"]


def test_a_utc_declined_is_converted_to_vietnam_time():
    slots = _clean(declined=["2026-09-20T09:00:00Z"])
    assert slots.declined == ["2026-09-20T16:00:00+07:00"]


def test_an_already_local_declined_is_kept_as_is():
    slots = _clean(declined=["2026-09-20T09:00:00+07:00"])
    assert slots.declined == ["2026-09-20T09:00:00+07:00"]


def test_a_date_only_declined_is_dropped_with_a_log():
    """"2026-09-20" vẫn qua fromisoformat nhưng không phải mốc giờ: render sẽ
    bịa ra 00:00 rồi đọc thành một giờ tiệm chưa từng chào."""
    with structlog.testing.capture_logs() as logs:
        slots = _clean(declined=["2026-09-20"], service="làm tóc")
    assert slots.declined == []
    assert [e for e in logs
            if e["event"] == "state_slot_dropped" and e["extra"]["field"] == "declined"]


def test_a_declined_in_the_past_is_dropped():
    """Hội thoại vắt từ hôm qua sang hôm nay: giờ đã trôi chỉ gây nhiễu."""
    with structlog.testing.capture_logs() as logs:
        slots = _clean(declined=["2026-09-18T09:00:00+07:00"], service="làm tóc")
    assert slots.declined == []
    assert [e for e in logs
            if e["event"] == "state_slot_dropped" and e["extra"]["field"] == "declined"]


@pytest.fixture
def process_tz_utc():
    """Đặt TZ của TIẾN TRÌNH về UTC, đúng như container python:3.11-slim.

    TIMEZONE trong .env chỉ vào settings.timezone, KHÔNG đổi giờ hệ thống —
    nên test naive-datetime chạy dưới TZ máy dev (+07) sẽ xanh một cách giả.
    """
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    _time.tzset()
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    _time.tzset()


def test_declined_is_timezone_correct_even_on_a_utc_host(process_tz_utc):
    """Bản sao của bốn test trên, chạy dưới TZ=UTC. Chạy đúng bất kể TZ máy."""
    assert _clean(declined=["2026-09-20T09:00:00"]).declined == ["2026-09-20T09:00:00+07:00"]
    assert _clean(declined=["2026-09-20T09:00:00Z"]).declined == ["2026-09-20T16:00:00+07:00"]
    assert _clean(declined=["2026-09-20T09:00:00+07:00"]).declined == ["2026-09-20T09:00:00+07:00"]
    assert _clean(declined=["2026-09-20"], service="làm tóc").declined == []
    # Và khâu render — chỗ khách thật sự thấy — nói đúng giờ khách đã lắc.
    assert "9 giờ sáng" in render_slots(_clean(declined=["2026-09-20T09:00:00"]))


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


def test_render_omits_fields_that_are_none():
    out = render_slots(ConversationSlots(intent="book", service="làm móng bột"))
    assert "Khách muốn: đặt lịch mới" in out
    assert "Dịch vụ: làm móng bột" in out
    assert "Ngày đang nhắm" not in out


def test_render_speaks_vietnamese_not_iso():
    out = render_slots(ConversationSlots(day="2026-09-20", time="15:00"))
    assert "Chủ Nhật 20/9" in out
    assert "3 giờ chiều" in out
    assert "2026-09-20" not in out


def test_render_lists_the_times_the_customer_turned_down():
    out = render_slots(ConversationSlots(
        declined=["2026-09-20T09:00:00+07:00", "2026-09-20T14:00:00+07:00"]))
    assert "9 giờ sáng" in out and "2 giờ chiều" in out


def test_render_is_empty_when_there_is_nothing():
    assert render_slots(None) == ""
    assert render_slots(ConversationSlots()) == ""


def test_render_never_uses_a_forbidden_honorific():
    """Xưng hô chốt cứng: không 'con', 'cô', 'chú', 'bác' ở bất cứ đâu."""
    out = render_slots(ConversationSlots(intent="cancel", service="làm tóc",
                                         day="2026-09-20", time="15:00"))
    for word in (" con ", " cô ", " chú ", " bác "):
        assert word not in f" {out} "
