"""Lỗi live B3 #2: khách nhắc NGƯỜI NHÀ ("nó", "thằng cháu"…) hỏi người đó có
rảnh không, hoặc nói sẽ hỏi người đó rồi nhắn lại.

- "khoan da, de chu hoi lai no co ranh khong" → agent gọi get_shop_status, đáp
  "chủ tiệm đang rảnh" (BOOKING_PROMPT rule 1: "whether the owner is busy").
- "ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi nhan lai em
  sau nha" → câu từ chối quyền riêng tư (rule 4).

Code nhận ra hai ca này (không LLM) → supervisor đưa vào booking không gọi
model, và agent đọc một dòng code viết trong khối bối cảnh.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph import supervisor
from app.agents.booking_graph.relatives import aside_line, asks_if_relative_free, defers
from tests.fakes import scripted

TURN4 = "khoan da, de chu hoi lai no co ranh khong"
TURN5 = "ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi nhan lai em sau nha"
TURN4_VI = "khoan đã, để chú hỏi lại nó có rảnh không"
TURN5_VI = "không, chú hỏi thằng cháu có rảnh không ạ. thôi để chú hỏi nó rồi nhắn lại em sau nha"


@pytest.mark.parametrize("text", [
    TURN4, TURN5, TURN4_VI, TURN5_VI,
    "ong xa chi co ranh khong thi chi moi dat",
    "để cô xem bà nhà có rảnh không đã",
    "con tôi có rảnh không thì tôi mới biết",
    "thằng Huy có rảnh hông em",
])
def test_a_relative_being_free_is_recognised(text):
    assert asks_if_relative_free(text)


@pytest.mark.parametrize("text", [
    "chủ tiệm có rảnh không em", "chi chu co ranh khong em", "tiem con ranh khong em",
    "còn rảnh không em", "chiều mai còn giờ rảnh không", "con gai chi muon lam mong",
    "mai 3 giờ chiều nha em", "em có rảnh không",
])
def test_the_owner_or_a_free_slot_is_not_a_relative(text):
    assert not asks_if_relative_free(text)


@pytest.mark.parametrize("text", [TURN4, TURN5, TURN4_VI, TURN5_VI, "để cô hỏi thằng Huy đã",
                                  "de chi tinh roi nhan lai em sau"])
def test_deferrals_are_recognised(text):
    assert defers(text)


@pytest.mark.parametrize("text", ["mai 3 giờ chiều nha em", "u giu luon em", "chủ tiệm có rảnh không"])
def test_not_a_deferral(text):
    assert not defers(text)


def test_aside_line_is_code_written_and_names_the_case():
    line = aside_line(TURN5)
    assert "[relative]" in line and "[deferral]" in line
    assert "không phải hỏi chủ tiệm" in line.lower()
    assert aside_line("mai 3 giờ chiều nha em") == ""


@pytest.mark.parametrize("text", [TURN4, TURN5, TURN4_VI, TURN5_VI])
async def test_supervisor_sends_them_to_booking_without_the_model(monkeypatch, text):
    """Model giả trả "shop" (đúng lỗi lượt 4): code phải đi trước, không hỏi model.
    (Model ném lỗi thì supervisor cũng ra booking — không chứng minh được gì.)"""
    sup = scripted([AIMessage("shop")], tags=("supervisor",))
    monkeypatch.setattr(supervisor, "build_chat_model", lambda **kw: sup)
    assert await supervisor.supervise({"messages": [HumanMessage(text)]}) == {"route": "booking"}
    assert sup.calls == []


# --- Lỗi live B4 #2: câu hoãn → câu đáp CODE viết, không qua model ------------
# Lượt 5 live B4 (a28dfb5): dòng [relative]/[deferral] có trong khối bối cảnh,
# supervisor đã route bằng code, vậy mà model vẫn chọn câu từ chối của rule 4.
from app.agents.booking_graph.relatives import deferral_reply  # noqa: E402


@pytest.mark.parametrize("text", [TURN4, TURN5, TURN4_VI, TURN5_VI, "để cô hỏi thằng Huy đã",
                                  "dạ vâng để chị gọi", "de chi xem lai roi nhan em sau nha"])
def test_a_plain_deferral_gets_a_code_reply(text):
    answer = deferral_reply(text, "anh Hùng", [])
    assert answer and answer.startswith("Dạ") and "anh Hùng" in answer and "nhắn" in answer
    assert "chỉ xem" not in answer and "rảnh" not in answer


@pytest.mark.parametrize("text", [
    "để chú hỏi nó, mà giữ giùm chú 2h chiều nha",       # có giờ + giữ
    "de chu hoi no roi dat luon nha em",                 # đặt
    "để chị xem lại, mà tiệm mấy giờ đóng cửa em",       # hỏi tiệm
    "de chi hoi lai, huy lich mai giup chi",             # hủy
    "để chị tính, dời qua thứ 6 được không",             # dời
    "thằng cháu có rảnh không em",                       # không hoãn
    "mai 3 giờ chiều nha em",
])
def test_a_deferral_with_a_request_still_goes_to_the_model(text):
    assert deferral_reply(text, "anh Hùng", []) is None


def test_two_deferrals_in_a_row_get_different_words():
    first = deferral_reply(TURN4, "anh Hùng", [])
    second = deferral_reply(TURN5, "anh Hùng", [first])
    third = deferral_reply(TURN5, "anh Hùng", [first, second])
    assert len({first, second, third}) == 3
