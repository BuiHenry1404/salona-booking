"""Lưới phủ quyết bằng code cho câu trả lời xác nhận.

Lưới KHÔNG quyết "đồng ý" — nó chỉ có quyền nói "chắc chắn KHÔNG phải đồng ý".
Câu nào lưới cho qua mới tới bộ phân loại (model). Vì vậy mọi câu dưới đây ở nhóm
"phải phủ quyết" phải ra non-None bất kể model nói gì: đó là các câu đã làm A ghi
lịch sai (G-01/G-02/G-03, tán gẫu `is_affirmative`, G-07) và bản không dấu.

`now` cố định là thứ Hai 19/10/2026 (F6): "mai" là thứ Ba, nên "thứ năm" luôn
là một ngày khác — test không phụ thuộc hôm nay là thứ mấy.
"""
from datetime import datetime

import pytest

from app.agents.booking_graph.reply import (mentions_other_time,
                                            strip_diacritics, veto_reason)
from app.core.clock import TZ

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)            # thứ Hai
START = datetime(2026, 10, 20, 15, 0, tzinfo=TZ)          # mai (thứ Ba) 3 giờ chiều
P_BOOK = {"kind": "book", "start_at": START.isoformat()}
P_MOVE = {"kind": "move", "start_at": START.isoformat()}
P_CANCEL = {"kind": "cancel", "start_at": START.isoformat()}


MUST_VETO_BOOK = [
    # brief 3.1
    "dạ ko em", "hổng em", "khoan đã", "để chị hỏi thằng Huy đã", "dạ vâng để chị gọi",
    "Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc",
    "giờ đó còn chỗ không em", "ừ mà 4 giờ được không", "ok nhưng thứ năm nha",
    "thôi hủy đi", "chua em", "k em",
    # bench: G-01 (ko/hông/khoan), G-03 (giờ khác), G-07 (sau vòng tool)
    "dạ hông", "dạ khoan", "dạ 4 giờ nha em", "à mà giờ đó còn chỗ không em",
    # bản không dấu (m3-amendments mục 7)
    "da ko em", "da khoan", "da hong", "de chi hoi thang Huy da", "da 4 gio nha em",
    "da vang de chi goi", "ma lam mong bot mat bao lau ha chi, viec gi cung phai tinh truoc cho chac",
    "ok nhung thu nam nha", "thoi huy di", "khong em",
    # hoãn / hỏi / giờ-ngày khác
    "để chị xem lại", "chị suy nghĩ thêm", "tính sau nha em", "để mai chị gọi lại",
    "ừ được mấy giờ vậy em", "vậy hả", "ừ mà ngày kia nha", "ok tuần sau nha em",
    "dạ 10 giờ sáng nha", "chủ nhật được không",
]

MUST_VETO_CANCEL = [
    "hủy cái nào vậy em?", "huy cai nao vay em?",            # G-02 hỏi lại
    "để chị hỏi thằng Huy đã", "de chi hoi thang Huy da",    # G-02 tên Huy
    "không hủy nữa", "thôi khỏi hủy", "khong huy",
]

NO_VETO_BOOK = [
    "ừ", "dạ đúng rồi", "ok em", "vâng", "dung roi", "ừ 3 giờ chiều mai đúng rồi",
    "được em", "dạ", "u", "dc", "ok", "Ừ", "vâng ạ", "dạ được", "chốt nha em",
    "ừ thứ ba nha", "dạ 3 giờ chiều nha em", "15h đúng rồi",
    # Lưới không quyết approve: câu lạc đề không có dấu hiệu phủ quyết vẫn ra None,
    # để bộ phân loại (không phải lưới) nói nó không phải đồng ý.
    "cho cô dùng thử",
]

NO_VETO_CANCEL = ["ừ hủy đi", "hủy đi em", "u huy di", "dạ hủy", "huy di em", "ok hủy"]


@pytest.mark.parametrize("text", MUST_VETO_BOOK)
def test_book_proposal_vetoes(text):
    assert veto_reason(text, P_BOOK, NOW) is not None, text


@pytest.mark.parametrize("text", MUST_VETO_BOOK)
def test_move_proposal_vetoes_the_same_sentences(text):
    assert veto_reason(text, P_MOVE, NOW) is not None, text


@pytest.mark.parametrize("text", MUST_VETO_CANCEL)
def test_cancel_proposal_vetoes(text):
    assert veto_reason(text, P_CANCEL, NOW) is not None, text


@pytest.mark.parametrize("text", NO_VETO_BOOK)
def test_book_proposal_lets_plain_agreement_through(text):
    assert veto_reason(text, P_BOOK, NOW) is None, text


@pytest.mark.parametrize("text", NO_VETO_CANCEL)
def test_huy_is_agreement_when_cancelling(text):
    """Bẫy #19: đang chốt HỦY thì "hủy" là đồng ý, không phải phủ định."""
    assert veto_reason(text, P_CANCEL, NOW) is None, text


@pytest.mark.parametrize("text", ["ừ hủy đi", "hủy đi em", "u huy di", "dạ hủy"])
def test_huy_stays_a_veto_when_booking(text):
    assert veto_reason(text, P_BOOK, NOW) == "negation"


@pytest.mark.parametrize("text, reason", [
    ("dạ ko em", "negation"),
    ("khoan đã", "negation"),
    ("để chị hỏi thằng Huy đã", "negation"),          # "Huy" bị bắt như "hủy" khi đặt
    ("dạ vâng để chị gọi", "deferral"),
    ("Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc", "question"),
    ("ok nhưng thứ năm nha", "other_time"),
    ("dạ 4 giờ nha em", "other_time"),
])
def test_reason_codes(text, reason):
    assert veto_reason(text, P_BOOK, NOW) == reason


def test_deferral_is_named_when_cancelling_huy():
    assert veto_reason("để chị hỏi thằng Huy đã", P_CANCEL, NOW) == "deferral"


def test_ok_is_not_caught_by_the_single_letter_k():
    assert veto_reason("ok", P_BOOK, NOW) is None
    assert veto_reason("okie", P_BOOK, NOW) is None


def test_start_at_may_be_a_datetime():
    assert veto_reason("dạ 4 giờ nha em", {"kind": "book", "start_at": START}, NOW) == "other_time"


class TestMentionsOtherTime:
    @pytest.mark.parametrize("text", [
        "4 giờ", "3 giờ sáng", "mốt nha", "hôm nay luôn", "ngày kia", "thứ năm",
        "chủ nhật", "tuần sau", "tuần tới", "thu nam", "chu nhat", "tuan sau", "4 gio",
    ])
    def test_other(self, text):
        assert mentions_other_time(text, START, NOW) is True, text

    @pytest.mark.parametrize("text", [
        "3 giờ", "3 giờ chiều", "15h", "mai", "ngày mai", "thứ ba", "thu ba",
        "ừ", "dạ đúng rồi", "3 gio chieu mai",
    ])
    def test_same(self, text):
        assert mentions_other_time(text, START, NOW) is False, text

    def test_next_week_is_fine_when_the_proposal_is_next_week(self):
        far = datetime(2026, 10, 28, 15, 0, tzinfo=TZ)
        assert mentions_other_time("tuần sau nha", far, NOW) is False


def test_strip_diacritics_handles_d_bar():
    assert strip_diacritics("Đúng rồi đó") == "Dung roi do"
    assert strip_diacritics("") == ""
    assert strip_diacritics(None) == ""


# --- fix round 1 (review M3) --------------------------------------------------

# Câu ĐỒNG Ý thật mà bản cũ phủ quyết oan vì bỏ dấu cả câu ("Hồng" → "hong",
# "đợi" → "doi", ...) hoặc vì "có gì" / "vậy nha" / "may quá".
REVIEW_FALSE_VETOES = [
    "dạ Hồng đồng ý", "dạ cô Hà đồng ý nha", "dạ được, lần đầu chị làm",
    "ok em, thời gian đó được", "ừ sáng chị đi chùa xong chị qua", "dạ được đợi chị nha",
    "dạ được, có gì em nhắn chị nha", "ừ 3 giờ chiều mai nha em, có gì nhắn chị nha",
    "ừ vậy nha em", "ok, may quá còn chỗ", "ừ vậy đi em",
]


@pytest.mark.parametrize("text", REVIEW_FALSE_VETOES)
def test_review_false_vetoes_now_pass_to_the_classifier(text):
    assert veto_reason(text, P_BOOK, NOW) is None, text


@pytest.mark.parametrize("text", [
    "dạ khong em", "dạ chua em", "vâng nhưng ko em", "dạ thoi em",   # nửa dấu nửa không
    "may gio xong vay", "mấy giờ xong vậy", "thế à vậy",            # câu hỏi thật
])
def test_mixed_and_real_questions_still_veto(text):
    assert veto_reason(text, P_BOOK, NOW) is not None, text


@pytest.mark.parametrize("text", ["ừ dời đi em", "dạ dời giúp chị", "ok đổi đi em",
                                  "ừ đổi qua giờ đó đi", "u doi di em", "ừ dời qua 3 giờ chiều mai nha"])
def test_doi_is_agreement_when_moving(text):
    assert veto_reason(text, P_MOVE, NOW) is None, text


@pytest.mark.parametrize("text", ["ok đổi đi em", "u doi di em"])
def test_doi_stays_a_veto_when_booking(text):
    assert veto_reason(text, P_BOOK, NOW) == "negation"


@pytest.mark.parametrize("text", ["không dời nữa", "thôi khỏi dời", "dời sang giờ khác"])
def test_a_real_negative_still_wins_when_moving(text):
    assert veto_reason(text, P_MOVE, NOW) == "negation"


@pytest.mark.parametrize("text", ["15h30", "dạ 3 rưỡi nha", "15:30 nha em", "3 giờ rưỡi chiều",
                                  "ừ 3 giờ 15 chiều", "3 gio ruoi"])
def test_minutes_other_than_the_proposal_veto(text):
    assert veto_reason(text, P_BOOK, NOW) == "other_time", text


@pytest.mark.parametrize("text", ["ok 15:00", "ừ 3 giờ 00 chiều", "ừ 3 giờ chiều 2 người"])
def test_same_minute_or_no_minute_does_not_veto(text):
    assert veto_reason(text, P_BOOK, NOW) is None, text


def test_live_bug4_bare_doi_on_a_move_reaches_the_classifier():
    """Lỗi live 4: "u doi di em" với đề xuất DỜI không bị lưới chặn (đúng —
    "dời" là động từ đồng ý của việc dời); chính bộ phân loại đọc nhầm thành
    `change` (sửa ở `_describe`). Với đề xuất ĐẶT MỚI thì "doi" là "đổi"."""
    assert veto_reason("u doi di em", P_MOVE, NOW) is None
    assert veto_reason("u doi di em", P_BOOK, NOW) == "negation"
    assert veto_reason("không phải hôm nay, mai đó em, 4 giờ chiều mai", P_MOVE, NOW) == "negation"
