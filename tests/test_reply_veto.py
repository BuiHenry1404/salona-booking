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

from app.agents.booking_graph.reply import (is_short_affirmation,
                                            mentions_other_time,
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


# --- Lỗi live B2 #2: nhiều lịch trong một câu, đặt LẦN LƯỢT --------------------
# Đề xuất hiện tại 9 giờ sáng thứ Tư 21/10; còn chờ 2 giờ chiều cùng ngày (cho
# thằng cháu). Câu vừa ĐỒNG Ý 9 giờ vừa nhắc giờ đang chờ không bị phủ quyết chỉ
# vì giờ đang chờ — mệnh đề nói về giờ đang chờ được bỏ khỏi phép kiểm hỏi/giờ
# khác. Phủ định/hoãn vẫn xét CẢ câu; mọi thứ khác vẫn fail-closed.
NINE = datetime(2026, 10, 21, 9, 0, tzinfo=TZ)
TWO_PM = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
P_QUEUE = {"kind": "book", "start_at": NINE.isoformat(), "queued": [TWO_PM.isoformat()]}
P_NO_QUEUE = {"kind": "book", "start_at": NINE.isoformat()}
HUNG_T2 = "dc em, 9h la cua chu. con 2h chieu cho thang chau thi sao"


@pytest.mark.parametrize("text", [
    HUNG_T2,
    "dc em 9h la cua chu con 2h chieu cho thang chau thi sao",     # không dấu câu
    "ừ được em, còn 2 giờ chiều cho thằng cháu thì sao?",
    "ok em. 2h chieu cho thang chau nua nha",
])
def test_agreeing_while_naming_a_queued_time_is_not_vetoed(text):
    assert veto_reason(text, P_QUEUE, NOW) is None, text


def test_the_same_reply_without_a_queue_is_still_vetoed():
    assert veto_reason(HUNG_T2, P_NO_QUEUE, NOW) is not None


@pytest.mark.parametrize("text,reason", [
    ("2h chieu di em", "other_time"),                          # chỉ nói giờ đang chờ
    ("2 giờ chiều trước đi em", "other_time"),
    ("dc em, con 3h chieu cho thang chau thi sao", "question"),  # 3 giờ không có trong hàng chờ
    ("ko em, con 2h chieu cho thang chau", "negation"),
    ("de chu hoi lai, con 2h chieu cho thang chau", "deferral"),
    ("dc em, 10h nha. con 2h chieu cho thang chau", "other_time"),  # giờ đề xuất bị cãi
    ("dc em, con 2h chieu thu 5 cho thang chau", "other_time"),    # ngày khác giờ đang chờ
    ("9h hả em? con 2h chieu cho thang chau", "question"),
])
def test_everything_else_stays_fail_closed_with_a_queue(text, reason):
    assert veto_reason(text, P_QUEUE, NOW) == reason, text


@pytest.mark.parametrize("text", MUST_VETO_BOOK)
def test_the_g_table_is_unchanged_with_an_unrelated_queue(text):
    """Hàng chờ ở ngày khác hẳn không được nới bất kỳ câu nào của bảng G."""
    far = {"kind": "book", "start_at": START.isoformat(),
           "queued": [datetime(2026, 10, 30, 11, 0, tzinfo=TZ).isoformat()]}
    assert veto_reason(text, far, NOW) is not None, text


# --- is_short_affirmation (lỗi LLM-02: định tuyến "Ok em" sau lượt booking) ---

@pytest.mark.parametrize("text", ["Ok em", "dạ được", "ừ nha", "oke", "Dc",
                                  "um", "ok a", "duoc roi"])
def test_short_affirmations_are_recognised(text):
    assert is_short_affirmation(text), text


@pytest.mark.parametrize("text", [
    "ok mà 4 giờ được không",       # đổi giờ, không phải đồng ý suông
    "dạ không",                      # có từ phủ định
    "chào em",                       # không có từ đồng ý nào
    "ok em cảm ơn nhiều nha chị",    # quá 4 từ / có từ ngoài lưới
    "",
])
def test_short_affirmations_rejects_everything_else(text):
    assert not is_short_affirmation(text), text


# --- `CONFIRM_VETO_MODE` (Task 15): "time_only" chỉ giữ "other_time" ----------

from app.agents.booking_graph.reply import decide  # noqa: E402

QUESTION = "Dạ em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho chị Lan, chị chốt giúp em nhé?"


async def test_time_only_mode_lets_negation_reach_the_classifier():
    """"dạ hông" ở chế độ "full" bị lưới chặn thẳng (xem bảng G ở trên);
    "time_only" phải để nó đi tới `label_fn` — lưới không còn ý kiến về
    phủ định/hoãn/hỏi, chỉ "giờ khác" mới bị chặn thẳng."""
    calls = []

    async def label_fn(proposal, bot_question, customer_text):
        calls.append(customer_text)
        return "reject"

    result = await decide("dạ hông", P_BOOK, QUESTION, NOW, label_fn=label_fn,
                          veto_mode="time_only")
    assert calls == ["dạ hông"]
    assert result == "reject"


async def test_time_only_mode_still_vetoes_a_different_time():
    """"other_time" vẫn bị lưới chặn thẳng ở "time_only" — code so giờ/ngày
    chắc chắn hơn model, giữ nguyên bất kể chế độ."""
    async def label_fn(proposal, bot_question, customer_text):
        raise AssertionError("label_fn must not be called for other_time")

    result = await decide("dạ 4 giờ nha em", P_BOOK, QUESTION, NOW,
                          label_fn=label_fn, veto_mode="time_only")
    assert result == "reject"


async def test_default_veto_mode_is_full_and_still_vetoes_negation():
    """An toàn: không truyền `veto_mode` thì vẫn là "full" — không đổi hành vi
    hiện có khi cờ chưa bật."""
    async def label_fn(proposal, bot_question, customer_text):
        raise AssertionError("label_fn must not be called in full mode")

    assert await decide("dạ hông", P_BOOK, QUESTION, NOW, label_fn=label_fn) == "reject"


async def test_time_only_mode_with_an_unclear_classifier_label_never_approves():
    """An toàn (yêu cầu bắt buộc của task): gõ "dạ hông" ở "time_only" đi tới
    bộ phân tích; bộ phân tích không chắc ("unclear") thì KHÔNG được ghi lịch."""
    async def label_fn(proposal, bot_question, customer_text):
        return "unclear"

    result = await decide("dạ hông", P_BOOK, QUESTION, NOW, label_fn=label_fn,
                          veto_mode="time_only")
    assert result == "unclear"


# --- review (finding #1): "time_only" phải bắt "giờ khác" ĐỘC LẬP, không để
# negation/deferral/question (lý do ĐẦU TIÊN của `veto_reason`) che mất nó ----

TIME_ONLY_STILL_OTHER_TIME = [
    "4 giờ được không em?",    # veto_reason() = "negation" (từ "không") che "4 giờ"
    "không, 4 giờ nha",        # idem
    "thôi để 5 giờ đi",        # veto_reason() = "negation" ("thôi") che "5 giờ"
    "ok mai 4 giờ được ko",    # veto_reason() = "negation" ("ko") che "4 giờ"
]


@pytest.mark.parametrize("text", TIME_ONLY_STILL_OTHER_TIME)
async def test_time_only_mode_still_vetoes_other_time_hidden_behind_a_veto_word(text):
    """Review finding #1: bốn câu trên đều có TỪ PHỦ ĐỊNH đứng trước giờ khác
    — `veto_reason` chỉ trả lý do ĐẦU TIÊN ("negation"), và luật cũ coi đó là
    "không phải other_time" nên bỏ qua ở "time_only", cho approve lọt qua.
    Đúng phải là: "time_only" tự hỏi `other_time_veto` ĐỘC LẬP và luôn reject,
    bất kể `label_fn` nói gì (kể cả khi nó nổ — không bao giờ được gọi)."""
    async def approving_label_fn(proposal, bot_question, customer_text):
        return "approve"

    async def raising_label_fn(proposal, bot_question, customer_text):
        raise AssertionError("label_fn must not be called when other_time vetoes")

    for label_fn in (approving_label_fn, raising_label_fn):
        result = await decide(text, P_BOOK, QUESTION, NOW, label_fn=label_fn,
                              veto_mode="time_only")
        assert result == "reject", (text, label_fn)


# --- review (finding #2): "time_only" không được để `is_go_ahead` tự approve
# một câu mà đề bài ĐẦY ĐỦ (full veto_reason) vẫn còn một lý do khác --------

TIME_ONLY_GO_AHEAD_WORDS_BUT_A_QUESTION = [
    ("cancel", "huy a?"),
    ("cancel", "hủy đi nha?"),
    ("move", "doi a?"),
    ("move", "dời đi nha?"),
]


@pytest.mark.parametrize("kind,text", TIME_ONLY_GO_AHEAD_WORDS_BUT_A_QUESTION)
async def test_time_only_mode_sends_a_disguised_question_to_the_classifier_not_go_ahead(kind, text):
    """Review finding #2: những câu này toàn CHỮ GIỤC ("hủy"/"dời" + tiểu từ)
    nên `is_go_ahead` trả True — nhưng chúng mang dấu "?" nên `veto_reason`
    ĐẦY ĐỦ (full) báo "question". "time_only" không phủ quyết "question" nữa,
    nhưng KHÔNG ĐƯỢC vì vậy mà chạy `is_go_ahead` (nó không biết về dấu hỏi) —
    phải đi tới `label_fn` như một câu chưa rõ ý."""
    proposal = {"kind": kind, "start_at": START.isoformat()}

    async def unclear_label_fn(proposal, bot_question, customer_text):
        return "unclear"

    result = await decide(text, proposal, QUESTION, NOW, label_fn=unclear_label_fn,
                          veto_mode="time_only")
    assert result == "unclear"


def test_reply_module_uses_zero_regex():
    import inspect
    from app.agents.booking_graph import reply
    source = inspect.getsource(reply)
    assert "import re" not in source and "re.compile" not in source


def test_mentions_other_time_also_checks_structured_mentioned_times():
    from app.agents.booking_graph.analyzer import MentionedTime
    # Kể cả khi text không chứa chữ số (vd viết bằng chữ), mentioned_times vẫn bắt được giờ lệch
    assert mentions_other_time(
        "dạ bốn giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=16, part_of_day="chieu")],
    ) is True
    assert mentions_other_time(
        "dạ ba giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=15, part_of_day="chieu")],
    ) is False

