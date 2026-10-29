"""Kiểm thử quyết định phản hồi xác nhận thuần semantic cho Node confirm và `reply.decide`.

Toàn bộ các bộ từ điển từ khóa chuỗi tĩnh (_NEG_PHRASES_*, _GO_VERBS, veto_reason)
đã được loại bỏ. Quyết định hoàn toàn dựa vào kết quả ngữ nghĩa có cấu trúc từ `TurnAnalysis`
(qua `label_fn`), với cơ chế fail-closed an toàn (lỗi/không rõ -> 'unclear').
"""
from datetime import datetime
from typing import Mapping, Optional

import pytest

from app.agents.booking_graph.analyzer import MentionedTime, TurnAnalysis
from app.agents.booking_graph.reply import (
    decide,
    is_short_affirmation,
    mentions_other_time,
    strip_diacritics,
)
from app.core.clock import TZ

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)            # thứ Hai
START = datetime(2026, 10, 20, 15, 0, tzinfo=TZ)          # mai (thứ Ba) 3 giờ chiều
P_BOOK = {"kind": "book", "start_at": START.isoformat()}
P_MOVE = {"kind": "move", "start_at": START.isoformat()}
P_CANCEL = {"kind": "cancel", "start_at": START.isoformat()}
QUESTION = "Dạ em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho chị Lan, chị chốt giúp em nhé?"

NON_APPROVING_PHRASES = [
    # brief 3.1
    "dạ ko em", "hổng em", "khoan đã", "để chị hỏi thằng Huy đã", "dạ vâng để chị gọi",
    "Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc",
    "giờ đó còn chỗ không em", "ừ mà 4 giờ được không", "ok nhưng thứ năm nha",
    "thôi hủy đi", "chua em", "k em",
    # bench: G-01 (ko/hông/khoan), G-03 (giờ khác), G-07 (sau vòng tool)
    "dạ hông", "dạ khoan", "dạ 4 giờ nha em", "à mà giờ đó còn chỗ không em",
    # bản không dấu
    "da ko em", "da khoan", "da hong", "de chi hoi thang Huy da", "da 4 gio nha em",
    "da vang de chi goi", "ma lam mong bot mat bao lau ha chi, viec gi cung phai tinh truoc cho chac",
    "ok nhung thu nam nha", "thoi huy di", "khong em",
    # hoãn / hỏi / giờ-ngày khác
    "để chị xem lại", "chị suy nghĩ thêm", "tính sau nha em", "để mai chị gọi lại",
    "ừ được mấy giờ vậy em", "vậy hả", "ừ mà ngày kia nha", "ok tuần sau nha em",
    "dạ 10 giờ sáng nha", "chủ nhật được không",
]


# --- Step 1: Kiểm tra các tình huống khách gõ chữ tự do thuần semantic ---------

@pytest.mark.asyncio
async def test_xem_lai_lich_hen_not_vetoed_as_deferral():
    """Khách gõ câu có từ 'xem lại' như 'chị xem lại lịch hẹn của chị với':
    Không bị veto hoãn, chuyển tiếp trực tiếp vào label_fn để phân tích ngữ nghĩa chính xác."""
    called_with = []

    async def label_fn(proposal, bot_question, customer_text):
        called_with.append(customer_text)
        # Bộ phân tích nhận diện đây là yêu cầu tra cứu (không phải defer)
        return "reject"

    decision = await decide(
        "chị xem lại lịch hẹn của chị với",
        P_BOOK,
        QUESTION,
        NOW,
        label_fn=label_fn,
    )
    assert called_with == ["chị xem lại lịch hẹn của chị với"], "Phải chuyển tiếp trực tiếp vào label_fn mà không bị code veto chặn"
    assert decision == "reject"


@pytest.mark.asyncio
async def test_indirect_agreement_identifies_approve():
    """Khách gõ câu đồng ý gián tiếp -> Nhận diện approve."""
    called_with = []

    async def label_fn(proposal, bot_question, customer_text):
        called_with.append(customer_text)
        return "approve"

    for text in ["thế thì chốt lịch giúp chị nha", "ừ 3 giờ chiều mai đúng rồi", "dạ làm giờ đó đi em"]:
        decision = await decide(text, P_BOOK, QUESTION, NOW, label_fn=label_fn)
        assert decision == "approve"

    assert len(called_with) == 3


@pytest.mark.asyncio
async def test_change_time_identifies_change():
    """Khách gõ câu đổi giờ -> Nhận diện change."""
    called_with = []

    async def label_fn(proposal, bot_question, customer_text):
        called_with.append(customer_text)
        return "change"

    for text in ["đổi sang 4 giờ chiều mai đi em", "thôi 5 giờ đi em"]:
        decision = await decide(text, P_BOOK, QUESTION, NOW, label_fn=label_fn)
        assert decision == "change"

    assert len(called_with) == 2


# --- Step 1 / Requirement 3: decide với label_fn không bao giờ tự ý approve câu non-approving ---

@pytest.mark.asyncio
@pytest.mark.parametrize("text", NON_APPROVING_PHRASES)
async def test_non_approving_phrases_never_approve_when_analyzer_rejects_or_unclear(text):
    """Mọi câu không phải đồng ý thuần tuý khi analyzer gắn nhãn reject/change/unclear
    thì decide phản ánh đúng nhãn đó và KHÔNG BAO GIỜ tự suy ra approve."""
    async def rejecting_label_fn(proposal, bot_question, customer_text):
        if "4 giờ" in customer_text or "thứ năm" in customer_text or "10 giờ" in customer_text:
            return "change"
        if "bao lâu" in customer_text or "còn chỗ không" in customer_text:
            return "unclear"
        return "reject"

    decision = await decide(text, P_BOOK, QUESTION, NOW, label_fn=rejecting_label_fn)
    assert decision != "approve", f"Câu '{text}' không được phép approve"
    assert decision in ("reject", "change", "unclear")


@pytest.mark.asyncio
async def test_decide_calls_label_fn_directly_without_string_veto():
    """decide gọi trực tiếp label_fn, không dùng bất kỳ heuristic so khớp chuỗi nào."""
    calls = []

    async def label_fn(proposal, bot_question, customer_text):
        calls.append(customer_text)
        return "reject"

    result = await decide("dạ ko em", P_BOOK, QUESTION, NOW, label_fn=label_fn)
    assert calls == ["dạ ko em"]
    assert result == "reject"


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["approve", "reject", "change", "unclear"])
async def test_decide_forwards_all_valid_reply_labels(label):
    async def label_fn(proposal, bot_question, customer_text):
        return label

    assert await decide("bất kỳ", P_BOOK, QUESTION, NOW, label_fn=label_fn) == label


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_label", [None, "yes", "no", "APPROVE", 123])
async def test_decide_falls_back_to_unclear_on_invalid_label(bad_label):
    async def label_fn(proposal, bot_question, customer_text):
        return bad_label

    assert await decide("ừ", P_BOOK, QUESTION, NOW, label_fn=label_fn) == "unclear"


@pytest.mark.asyncio
async def test_decide_falls_back_to_unclear_on_exception():
    async def blowing_label_fn(proposal, bot_question, customer_text):
        raise RuntimeError("LLM service timeout")

    assert await decide("ừ", P_BOOK, QUESTION, NOW, label_fn=blowing_label_fn) == "unclear"


# --- mentions_other_time: Tiện ích logic thời gian có cấu trúc -----------------

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


def test_mentions_other_time_also_checks_structured_mentioned_times():
    # Kể cả khi text không chứa chữ số (vd viết bằng chữ), mentioned_times vẫn bắt được giờ lệch
    assert mentions_other_time(
        "dạ bốn giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=16, part_of_day="chieu")],
    ) is True
    assert mentions_other_time(
        "dạ ba giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=15, part_of_day="chieu")],
    ) is False


def test_strip_diacritics_handles_d_bar():
    assert strip_diacritics("Đúng rồi đó") == "Dung roi do"
    assert strip_diacritics("") == ""
    assert strip_diacritics(None) == ""


# --- is_short_affirmation: dùng cho supervisor --------------------------------

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


# --- Ngân sách regex: module reply tuyệt đối 0 regex --------------------------

def test_reply_module_uses_zero_regex():
    import inspect
    from app.agents.booking_graph import reply
    source = inspect.getsource(reply)
    assert "import re" not in source and "re.compile" not in source
