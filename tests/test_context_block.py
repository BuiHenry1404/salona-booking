from datetime import datetime

from app.agents.booking_graph.context import (build_context_block,
                                              format_vi_datetime)
from app.core.clock import TZ
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.models.user import User


def a_user(name="Nguyễn Thị Lan"):
    return User(phone="0912345678", hashed_password="h", full_name=name, role="user")


def an_appointment():
    return Appointment(
        user_id="u1", user_name="Nguyễn Thị Lan", phone="0912345678",
        start_at=datetime(2026, 8, 7, 8, 0, tzinfo=TZ),
        duration_minutes=60, note="làm tóc",
    )


# `build_context_block` không còn tham số `memories`: tầng ngữ nghĩa đã bỏ cùng
# Mem0 (xem README, "số 2 bỏ trống"). Câu "tin phần này" vẫn giữ — nó chống cả
# chuyện model nhặt nhầm tên từ lịch sử chat.


def test_block_tells_the_model_what_day_it_is_today():
    """Không có dòng này thì "mai 3h chiều" là câu không giải được.

    Tool đòi `day` dạng YYYY-MM-DD và `start_at` dạng ISO, nhưng model không có
    cách nào biết hôm nay là ngày mấy. Thiếu mốc, nó suy ra từ dữ liệu huấn
    luyện và đặt lịch lệch cả năm — mà tool chỉ nhận được một chuỗi ngày hợp lệ
    nên không có gì để chặn.
    """
    now = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [], now=now)

    assert "2026-08-07" in block          # dạng máy, để model tự cộng ngày
    assert "Thứ Sáu" in block             # dạng người, để model nói lại cho khách
    assert "2:30 chiều" in block


def test_the_date_line_comes_first():
    """Đứng cuối khối thì model hay bỏ qua khi khối dài."""
    now = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [], now=now)
    assert block.splitlines()[0].startswith("Bây giờ là")


def test_block_contains_the_real_name_from_the_database():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "Nguyễn Thị Lan" in block
    assert "0912345678" in block


def test_block_says_it_outranks_anything_else_in_the_prompt():
    """Bảo vệ chống lỗi gọi nhầm tên: lịch sử chat có thể chứa tên người khác
    (khách nhắc tên con cháu). Khối này phải tự tuyên bố là nguồn đúng."""
    block = build_context_block(a_user("Nguyễn Thị Lan"), ShopStatusView(is_busy=False), [])
    assert "Nguyễn Thị Lan" in block
    assert "tin phần" in block.lower() or "ưu tiên" in block.lower()


def test_busy_status_gives_a_finish_TIME_not_a_countdown():
    """AI sẽ nhắc lại câu này cho khách, và nó nằm lại trong lịch sử chat.
    "Còn 30 phút" đọc lại sau một tiếng là sai hẳn."""
    block = build_context_block(
        a_user(),
        ShopStatusView(
            is_busy=True,
            busy_until=datetime(2026, 8, 7, 15, 30, tzinfo=TZ),
            minutes_left=30,
        ),
        [],
        now=datetime(2026, 8, 7, 15, 0, tzinfo=TZ),
    )
    assert "bận" in block.lower()
    assert "3:30 chiều" in block
    assert "30 phút" not in block


def test_free_status_is_written_in_words():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "rảnh" in block.lower()


def test_upcoming_appointments_appear_with_vietnamese_dates():
    block = build_context_block(
        a_user(), ShopStatusView(is_busy=False), [an_appointment()]
    )
    assert "làm tóc" in block
    assert "Thứ Sáu" in block  # 7/8/2026 là Thứ Sáu


def test_no_upcoming_appointments_is_stated_explicitly():
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
    assert "chưa có lịch" in block.lower()


def test_format_vi_datetime():
    assert format_vi_datetime(datetime(2026, 8, 7, 15, 0, tzinfo=TZ)) == "Thứ Sáu 7/8, 3:00 chiều"
    assert format_vi_datetime(datetime(2026, 8, 7, 9, 30, tzinfo=TZ)) == "Thứ Sáu 7/8, 9:30 sáng"
