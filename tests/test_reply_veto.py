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
    mentions_other_time,
    other_time,
    resolve_label,
    spoken_times,
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
    """Khách gõ câu đổi giờ -> lưới code trả change, KHÔNG cần gọi model."""
    called_with = []

    async def label_fn(proposal, bot_question, customer_text):
        called_with.append(customer_text)
        return "approve"

    for text in ["đổi sang 4 giờ chiều mai đi em", "thôi 5 giờ đi em"]:
        decision = await decide(text, P_BOOK, QUESTION, NOW, label_fn=label_fn)
        assert decision == "change"

    assert called_with == []


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


# --- Lưới code giờ/ngày khác TRƯỚC nhãn model (lỗi 1, 2, 11, 12) -------------

@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["ok mà 4 giờ nha", "dạ 4 giờ nha em", "ok nhưng thứ năm nha",
                                  "ừ mà ngày kia nha", "ok tuần sau nha em", "da 4 gio nha em"])
async def test_text_time_vetoes_approve_without_calling_model(text):
    calls = []

    async def label_fn(*a):
        calls.append(a)
        return "approve"

    assert await decide(text, P_BOOK, QUESTION, NOW, label_fn=label_fn) == "change"
    assert calls == []


@pytest.mark.asyncio
async def test_same_time_restated_still_reaches_the_model():
    async def label_fn(*a):
        return "approve"

    assert await decide("ừ 3 giờ chiều mai nha", P_BOOK, QUESTION, NOW, label_fn=label_fn) == "approve"


def test_other_time_ignores_clauses_about_the_queued_slot():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("ừ 9 giờ, còn 2 giờ chiều cho cháu thì sao", p, NOW) is False


def test_other_time_still_catches_a_new_time_while_queued():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("ừ nhưng dời qua 10 giờ", p, NOW) is True


def test_other_time_skips_structured_items_matching_the_queue():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert other_time("", p, NOW, [MentionedTime(hour=2, part_of_day="chieu")]) is False
    assert other_time("", p, NOW, [MentionedTime(hour=10)]) is True


def test_other_time_accepts_dict_mentioned_times():
    """`resolve_label` nhận `model_dump()` — mục trong `mentioned_times` là dict."""
    assert other_time("", P_BOOK, NOW, [{"hour": 4, "part_of_day": "chieu"}]) is True
    assert other_time("", P_BOOK, NOW, [{"hour": 3, "part_of_day": "chieu", "day_offset": 1}]) is False


# Đầu ra Azure THẬT đã ghi (đề xuất mai 15:00 = `P_BOOK`): model gắn "change"
# cho câu chỉ nhắc lại đúng giờ đề xuất.
LIVE_RESTATE_1 = {"reply_to_proposal": "change", "aside": None,
                  "mentioned_times": [{"hour": 3, "minute": 0, "part_of_day": "chieu", "day_offset": 1}]}
LIVE_RESTATE_2 = {"reply_to_proposal": "change", "aside": None,
                  "mentioned_times": [{"hour": 15, "minute": 0, "part_of_day": "chieu", "day_offset": 1}]}


@pytest.mark.parametrize("analysis", [LIVE_RESTATE_1, LIVE_RESTATE_2])
def test_change_label_on_restated_proposal_time_becomes_unclear(analysis):
    assert resolve_label(analysis, "ừ 3 giờ chiều mai nha", P_BOOK, NOW) == "unclear"


def test_change_restating_the_time_but_naming_a_service_stays_change():
    """F2: nhắc đúng giờ nhưng đổi dịch vụ — vẫn là đổi, không hỏi lại chỉ về giờ."""
    a = {**LIVE_RESTATE_1, "service": "cắt tóc"}
    assert resolve_label(a, "ok 3 giờ chiều mai, đổi sang cắt tóc nha", P_BOOK, NOW) == "change"


def test_change_to_a_time_that_is_also_queued_stays_change():
    """F2: "nhắc lại giờ" xét trên câu GỐC — mệnh đề trùng giờ chờ không bị bỏ."""
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    a = {"reply_to_proposal": "change", "aside": None, "service": None,
         "mentioned_times": [{"hour": 9}, {"hour": 2, "part_of_day": "chieu"}]}
    assert resolve_label(a, "9 giờ sớm quá, 2 giờ chiều đi", p, NOW) == "change"


def test_approve_naming_only_a_queued_time_is_unclear():
    """F3: "ừ, 2 giờ chiều" khi đề xuất 9 giờ, giờ chờ 14 giờ — không ghi 9 giờ."""
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    a = {"reply_to_proposal": "approve", "aside": None, "service": None,
         "mentioned_times": [{"hour": 2, "part_of_day": "chieu"}]}
    assert resolve_label(a, "ừ, 2 giờ chiều", p, NOW) == "unclear"


def test_approve_naming_the_proposal_and_a_queued_time_stays_approve():
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    a = {"reply_to_proposal": "approve", "aside": None, "service": None,
         "mentioned_times": [{"hour": 9}, {"hour": 2, "part_of_day": "chieu"}]}
    assert resolve_label(a, "ừ 9 giờ, còn 2 giờ chiều cho cháu thì sao", p, NOW) == "approve"


def test_spoken_times_of_queue_stripped_text_names_only_this_proposal():
    """F4: `asked` không lẫn giờ của lịch đang chờ."""
    p = {**P_BOOK, "start_at": START.replace(hour=9).isoformat(),
         "queued": [START.replace(hour=14).isoformat()]}
    assert spoken_times("dời qua 10 giờ, còn 2 giờ chiều cho cháu", p, NOW) == "10 giờ"


def test_change_without_any_time_stays_change():
    a = {"reply_to_proposal": "change", "aside": None, "mentioned_times": []}
    assert resolve_label(a, "đổi giờ khác đi em", P_BOOK, NOW) == "change"


def test_relative_free_with_approve_is_unclear():
    a = {"reply_to_proposal": "approve", "aside": "relative_free", "mentioned_times": []}
    assert resolve_label(a, "ok để chị hỏi nó rảnh không", P_BOOK, NOW) == "unclear"


@pytest.mark.parametrize("aside", ["defer_only", "defer_with_request"])
def test_defer_aside_is_reject(aside):
    a = {"reply_to_proposal": "approve", "aside": aside, "mentioned_times": []}
    assert resolve_label(a, "để chị hỏi thằng Huy đã", P_BOOK, NOW) == "reject"


def test_structured_other_time_is_change_even_if_model_approves():
    a = {"reply_to_proposal": "approve", "aside": None,
         "mentioned_times": [{"hour": 4, "part_of_day": "chieu"}]}
    assert resolve_label(a, "ok bốn giờ nha", P_BOOK, NOW) == "change"


def test_unknown_label_is_unclear():
    a = {"reply_to_proposal": None, "aside": None, "mentioned_times": []}
    assert resolve_label(a, "ừm", P_BOOK, NOW) == "unclear"


@pytest.mark.parametrize("text,expected", [("ok mà 4 giờ nha", "4 giờ"),
                                           ("10 giờ 30 sáng nha", "10 giờ 30 sáng"),
                                           ("thôi em, không làm nữa", None)])
def test_spoken_times_is_written_by_code(text, expected):
    assert spoken_times(text) == expected


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
