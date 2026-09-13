from datetime import datetime

from app.agents.booking_graph.context import (address_phrase,
                                              build_context_block,
                                              derive_address, display_name,
                                              format_vi_datetime,
                                              format_vi_hhmm)
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
    assert "2 giờ rưỡi chiều" in block


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
    assert "3 giờ rưỡi chiều" in block
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


class TestFormatViDatetime:
    """Cụ già đọc "3 giờ chiều", không đọc "3:00 chiều".

    Câu này đi thẳng vào lời thoại: node confirm dùng nó cho câu chốt lịch, và
    khối bối cảnh dùng nó cho danh sách lịch. Dấu hai chấm là cách máy viết giờ,
    không phải cách người nói.
    """

    def test_a_whole_hour_drops_the_minutes_entirely(self):
        assert format_vi_datetime(
            datetime(2026, 8, 7, 15, 0, tzinfo=TZ)) == "Thứ Sáu 7/8, 3 giờ chiều"

    def test_half_past_is_said_as_ruoi(self):
        """Không ai nói "9 giờ 30" — người ta nói "9 rưỡi". SHOP_PROMPT cũng
        đang lấy "3 giờ rưỡi chiều" làm ví dụ mẫu."""
        assert format_vi_datetime(
            datetime(2026, 8, 7, 9, 30, tzinfo=TZ)) == "Thứ Sáu 7/8, 9 giờ rưỡi sáng"

    def test_other_minutes_are_spelled_out_after_gio(self):
        assert format_vi_datetime(
            datetime(2026, 8, 7, 13, 45, tzinfo=TZ)) == "Thứ Sáu 7/8, 1 giờ 45 chiều"

    def test_evening_keeps_its_period(self):
        assert format_vi_datetime(
            datetime(2026, 8, 7, 19, 15, tzinfo=TZ)) == "Thứ Sáu 7/8, 7 giờ 15 tối"

    def test_no_colon_ever_appears(self):
        """Hàng rào: một dấu hai chấm lọt vào là lại thành giọng máy."""
        for hour in range(8, 20):
            for minute in (0, 15, 30, 45):
                out = format_vi_datetime(datetime(2026, 8, 7, hour, minute, tzinfo=TZ))
                assert ":" not in out, out


class TestDeriveAddress:
    """Tiền tố trong full_name là tín hiệu giới tính DUY NHẤT đang có —
    User model không có trường giới tính. Map nó, đừng vứt đi."""

    def test_co_maps_to_chi(self):
        assert derive_address("Cô Lan") == ("chị", "Lan")

    def test_ba_maps_to_chi(self):
        assert derive_address("Bà Sáu") == ("chị", "Sáu")

    def test_chu_maps_to_anh(self):
        assert derive_address("Chú Hùng") == ("anh", "Hùng")

    def test_ong_maps_to_anh(self):
        assert derive_address("Ông Tư") == ("anh", "Tư")

    def test_bac_is_ambiguous_but_prefix_still_stripped(self):
        # "Bác" không cho biết giới tính, nhưng vẫn phải cắt khỏi tên —
        # prompt cấm tuyệt đối nói "bác".
        assert derive_address("Bác Bảy") == ("anh chị", "Bảy")

    def test_name_without_prefix_falls_back(self):
        assert derive_address("Nguyễn Thị Lan") == ("anh chị", "")

    def test_none_falls_back(self):
        assert derive_address(None) == ("anh chị", "")

    def test_single_word_name_falls_back(self):
        assert derive_address("Lan") == ("anh chị", "")


class TestAddressPhrase:
    def test_gendered_prefix_keeps_the_name(self):
        assert address_phrase("Cô Lan") == "chị Lan"
        assert address_phrase("Chú Hùng") == "anh Hùng"

    def test_ambiguous_drops_the_name(self):
        # "anh chị Bảy" không ai nói. Mơ hồ thì gọi trống.
        assert address_phrase("Bác Bảy") == "anh chị"
        assert address_phrase(None) == "anh chị"


class TestDisplayName:
    def test_prefix_is_stripped(self):
        assert display_name("Cô Lan") == "Lan"
        assert display_name("Bác Bảy") == "Bảy"

    def test_name_without_prefix_kept_whole(self):
        assert display_name("Nguyễn Thị Lan") == "Nguyễn Thị Lan"

    def test_missing_name(self):
        assert display_name(None) == "khách"


class TestContextBlockAddress:
    def _block(self, full_name):
        user = User(phone="0912345678", hashed_password="x", full_name=full_name)
        return build_context_block(user, ShopStatusView(is_busy=False), [])

    def test_block_pins_one_form_of_address(self):
        # Model chỉ việc chép dòng này, không tự chọn register mỗi lượt.
        assert "Gọi khách là: chị Lan" in self._block("Cô Lan")

    def test_block_never_leaks_the_forbidden_register(self):
        # BOOKING_PROMPT cấm nói "cô/chú/bác"; khối bối cảnh không được
        # đưa chính những chữ đó vào miệng model.
        block = self._block("Bác Bảy")
        for cam in ("Cô ", "Chú ", "Bác "):
            assert cam not in block


class TestFormatVietnameseHhmm:
    """Giờ mở cửa lưu dạng chuỗi 'HH:MM', không phải datetime — nhưng đọc
    lên vẫn phải nghe như người nói, không phải '08:00'."""

    def test_morning(self):
        assert format_vi_hhmm("08:00") == "8 giờ sáng"

    def test_afternoon(self):
        assert format_vi_hhmm("14:00") == "2 giờ chiều"

    def test_noon_is_afternoon(self):
        assert format_vi_hhmm("12:00") == "12 giờ chiều"

    def test_evening(self):
        assert format_vi_hhmm("19:00") == "7 giờ tối"

    def test_half_past_reads_as_ruoi(self):
        assert format_vi_hhmm("09:30") == "9 giờ rưỡi sáng"

    def test_odd_minutes(self):
        assert format_vi_hhmm("13:45") == "1 giờ 45 chiều"
