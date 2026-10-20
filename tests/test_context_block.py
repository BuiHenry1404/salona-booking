from datetime import datetime
import pytest

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

    def test_noon_is_trua_not_chieu(self):
        """Sửa 2026-09-20, lật `test_noon_is_afternoon` cũ.

        "12 giờ chiều" không phải tiếng Việt: "trưa" tồn tại chính xác để phân
        biệt 12h ban ngày với 12h đêm. Không phải đánh đổi sản phẩm — chính
        parser của dự án đã hiểu đầu vào "mai 12 giờ trưa"
        (`tests/test_timeparse.py`), tức hệ ĐỌC VÀO "trưa" mà NÓI RA "chiều".

        Ca này chạm khách thường xuyên: tiệm mở 08:00–19:00 không nghỉ trưa,
        bước 15 phút, nên 12:00/12:15/12:30/12:45 đều là ô đặt được."""
        assert format_vi_hhmm("12:00") == "12 giờ trưa"
        assert format_vi_hhmm("12:30") == "12 giờ rưỡi trưa"
        assert format_vi_hhmm("12:45") == "12 giờ 45 trưa"

    def test_one_pm_is_still_chieu(self):
        """Biên dưới của "chiều" không đổi — chỉ đúng 12 giờ mới là trưa."""
        assert format_vi_hhmm("13:00") == "1 giờ chiều"

    def test_midnight_is_dem_not_zero_oclock(self):
        """"0 giờ sáng" không ai nói. Ngoài giờ mở cửa nên hiếm, nhưng tới
        được qua `slots.time` khi khách nhắc nửa đêm mà chưa chốt ngày."""
        assert format_vi_hhmm("00:00") == "12 giờ đêm"
        assert format_vi_hhmm("00:30") == "12 giờ rưỡi đêm"

    def test_evening(self):
        assert format_vi_hhmm("19:00") == "7 giờ tối"

    def test_half_past_reads_as_ruoi(self):
        assert format_vi_hhmm("09:30") == "9 giờ rưỡi sáng"

    def test_odd_minutes(self):
        assert format_vi_hhmm("13:45") == "1 giờ 45 chiều"


class TestUpcomingAppointmentsCarryTheirId:
    """BUG-2 (CONTEXT.md, checkpoint 2026-09-14): `cancel_appointment` cần id,
    mà lịch sử chat chỉ lưu câu hỏi và câu đáp — sang lượt mới model không còn
    id nào và tự bịa. Khối bối cảnh dựng lại mỗi lượt từ DB, nên id ở đây là
    luôn đúng và luôn có, không phụ thuộc lượt trước gọi tool gì.
    """

    def test_each_upcoming_line_ends_with_the_id(self):
        appt = an_appointment()
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [appt])
        assert f"[id: {appt.id}, iso: 2026-08-07T08:00:00+07:00]" in block

    def test_the_id_sits_on_the_same_line_as_the_time(self):
        appt = an_appointment()
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [appt])
        line = next(l for l in block.splitlines() if str(appt.id) in l)
        assert "Thứ Sáu 7/8" in line and "làm tóc" in line


class TestLastReplyIsQuotedSoItIsNotRepeated:
    """Chạy thật 2026-09-14: hai lượt từ chối liên tiếp ra nguyên một câu. Luật
    trong prompt (kể cả viết hoa) và temperature 0.6 đều không đổi được — model
    neo vào câu của chính nó trong lịch sử. Khối bối cảnh đứng ngay trước câu
    khách, nên đặt lệnh "đừng nói lại nguyên câu này" ở đây, kèm chính câu đó.
    """

    def test_the_previous_reply_is_quoted_with_a_do_not_repeat_line(self):
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [],
                                    last_reply="Em chỉ xem lịch của chị Thắm thôi.")
        assert "Em chỉ xem lịch của chị Thắm thôi." in block
        assert "không nói lại nguyên văn" in block.lower()

    def test_no_previous_reply_means_no_line(self):
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [])
        assert "nguyên văn" not in block.lower()

    def test_a_long_reply_is_cut_and_flattened(self):
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [],
                                    last_reply="dòng một\ndòng hai " + "x" * 500)
        line = next(l for l in block.splitlines() if "dòng một" in l)
        assert "\n" not in line and len(line) < 320

    def test_the_line_sits_before_the_trust_line(self):
        block = build_context_block(a_user(), ShopStatusView(is_busy=False), [],
                                    last_reply="Câu cũ.")
        lines = block.splitlines()
        assert lines.index(next(l for l in lines if "Câu cũ." in l)) < len(lines) - 1
        assert lines[-1].startswith("Nếu có gì")


# --- dòng "khách vừa KHÔNG chốt" ở lượt kế (lỗi live 3/5/6) -------------------

class TestDeclinedLineInLoadContext:
    @pytest.fixture
    async def lan(self, test_db):
        from app.services.auth import AuthService
        return await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    @staticmethod
    def tomorrow_at(hour):
        from datetime import timedelta
        from app.core.clock import TZ
        local = datetime.now(TZ) + timedelta(days=1)
        return local.replace(hour=hour, minute=0, second=0, microsecond=0)

    async def test_a_fresh_rejection_is_in_the_block(self, test_db, lan):
        from app.agents.booking_graph.context import load_context
        from app.services.proposal import ProposalService
        proposals = ProposalService(test_db)
        p = await proposals.create(lan, "book", self.tomorrow_at(15), "làm tóc")
        await proposals.reject(lan, str(p.id))
        block = (await load_context(test_db, lan, "sao em"))["context_block"]
        assert "KHÔNG chốt" in block and "[declined: book" in block

    async def test_a_newer_proposal_hides_the_old_decline(self, test_db, lan):
        from app.agents.booking_graph.context import load_context
        from app.services.proposal import ProposalService
        proposals = ProposalService(test_db)
        p = await proposals.create(lan, "book", self.tomorrow_at(15), "làm tóc")
        await proposals.reject(lan, str(p.id))
        await proposals.create(lan, "book", self.tomorrow_at(16), "làm tóc")
        block = (await load_context(test_db, lan, "sao em"))["context_block"]
        assert "declined" not in block

    async def test_a_customer_message_in_the_same_millisecond_as_the_close_ends_it(self):
        """Mongo lưu mốc giờ ở độ phân giải mili-giây. Câu khách ghi cùng
        mili-giây với lúc đề xuất đóng (máy nhanh, test chạy sát nhau) vẫn là
        câu SAU lúc từ chối: câu của lượt từ chối luôn đứng trước một lượt LLM
        phân loại, không bao giờ cùng mili-giây. So `>` thì dòng declined sống
        thêm một lượt và test trên chập chờn theo tốc độ máy."""
        from datetime import timedelta
        from types import SimpleNamespace
        from app.agents.booking_graph.context import _recent_decline
        from app.core.clock import now_ms
        closed = now_ms()
        latest = SimpleNamespace(status="rejected", updated_at=closed, kind="book",
                                 start_at=closed + timedelta(days=1), note=None)

        class Conversations:
            async def _all_messages(self, user_id):
                return [SimpleNamespace(role="user", created_at=closed)]

        assert await _recent_decline(Conversations(), "u1", latest) is None

    async def test_a_customer_message_after_the_decline_ends_it(self, test_db, lan):
        from app.agents.booking_graph.context import load_context
        from app.core.clock import now_ms
        from app.services.conversation import ConversationService
        from app.services.proposal import ProposalService
        proposals = ProposalService(test_db)
        p = await proposals.create(lan, "book", self.tomorrow_at(15), "làm tóc")
        await proposals.reject(lan, str(p.id))
        await ConversationService(test_db).append(str(lan.id), "user", "vậy giờ sao em",
                                                  created_at=now_ms())
        block = (await load_context(test_db, lan, "à mà thôi"))["context_block"]
        assert "declined" not in block

    async def test_an_old_decline_is_not_shown(self, test_db, lan, monkeypatch):
        from datetime import timedelta
        from app.agents.booking_graph import context
        from app.core.clock import now_utc
        from app.services.proposal import ProposalService
        proposals = ProposalService(test_db)
        p = await proposals.create(lan, "book", self.tomorrow_at(15), "làm tóc")
        await proposals.reject(lan, str(p.id))
        later = now_utc() + timedelta(minutes=context.DECLINED_MAX_AGE_MINUTES + 1)
        monkeypatch.setattr(context, "now_utc", lambda: later)
        block = (await context.load_context(test_db, lan, "sao em"))["context_block"]
        assert "declined" not in block

    async def test_move_line_names_both_times_and_says_the_old_one_stays(self, test_db, lan):
        from app.agents.booking_graph.context import format_vi_datetime, load_context
        from app.services.appointment import AppointmentService
        from app.services.proposal import ProposalService
        appt = await AppointmentService(test_db).create(lan, self.tomorrow_at(15), note="x")
        proposals = ProposalService(test_db)
        p = await proposals.create(lan, "move", self.tomorrow_at(16), None, target=appt)
        await proposals.supersede(lan, str(p.id))
        block = (await load_context(test_db, lan, "sao em"))["context_block"]
        line = next(x for x in block.splitlines() if "declined" in x)
        assert format_vi_datetime(self.tomorrow_at(15)) in line
        assert format_vi_datetime(self.tomorrow_at(16)) in line
        assert "lịch cũ vẫn giữ nguyên" in line
        assert f"[declined: move, iso: {self.tomorrow_at(16).isoformat()}]" in line
