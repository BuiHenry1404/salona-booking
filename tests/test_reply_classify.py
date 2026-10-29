"""`reply.decide` (lưới code trước, `label_fn` sau) và `reply._describe`.

`classify_reply`/`ReplyDecision` đã bị xoá (Task 6, lỗi 9): nhãn ngữ nghĩa giờ
đến từ `TurnAnalysis` của `analyzer.analyze_turn` — `label_fn` ở đây là một
hàm giả đứng thay cho nó.
"""
from datetime import datetime

import pytest

from app.agents.booking_graph import reply
from app.agents.booking_graph.reply import decide
from app.core.clock import TZ

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
PROPOSAL = {"kind": "book", "start_at": datetime(2026, 10, 20, 15, 0, tzinfo=TZ).isoformat()}
QUESTION = "Dạ em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho chị Lan, chị chốt giúp em nhé?"


# --- lỗi live 4: "u doi di em" với đề xuất DỜI bị đọc thành `change` ----------

MOVE = {"kind": "move", "start_at": datetime(2026, 10, 20, 16, 0, tzinfo=TZ).isoformat()}
CANCEL = {"kind": "cancel", "start_at": datetime(2026, 10, 20, 15, 0, tzinfo=TZ).isoformat()}


@pytest.mark.parametrize("proposal", [MOVE, CANCEL])
def test_describe_move_says_go_ahead_is_approve(proposal):
    """Không dấu, "doi" vừa là "dời" (đồng ý dời) vừa là "đổi". Một câu tiếng
    Anh (không ví dụ tiếng Việt — bẫy #15/#16): giục làm đúng việc đó bằng
    chính động từ của nó là đồng ý."""
    text = reply._describe(proposal)
    assert "go ahead" in text
    assert "own verb" in text
    assert "means approve" in text


def test_describe_book_has_no_go_ahead_sentence():
    assert "go ahead" not in reply._describe(PROPOSAL)


def test_describe_tells_the_classifier_about_the_queue():
    nine = datetime(2026, 10, 21, 9, 0, tzinfo=TZ)
    two = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    text = reply._describe({"kind": "book", "start_at": nine.isoformat(),
                            "queued": [two.isoformat()]})
    assert "waiting" in text and two.isoformat() in text
    assert "does not change this proposal" in text
    assert "waiting" not in reply._describe(PROPOSAL)


# --- Task 12b: nhãn cuối của `decide` đến từ `label_fn` (bộ phân tích) --------

def label_fn_returning(label, calls=None):
    async def fn(proposal, bot_question, customer_text):
        if calls is not None:
            calls.append((proposal, bot_question, customer_text))
        if isinstance(label, Exception):
            raise label
        return label
    return fn


async def test_typed_reply_uses_label_fn():
    """`decide` gọi trực tiếp `label_fn` để lấy quyết định ngữ nghĩa."""
    calls = []
    fn = label_fn_returning("approve", calls)

    assert await decide("ừ", PROPOSAL, QUESTION, NOW, label_fn=fn) == "approve"
    assert calls == [(PROPOSAL, QUESTION, "ừ")]


@pytest.mark.parametrize("bad", [RuntimeError("azure"), None, "yes", "APPROVE"])
async def test_label_fn_failure_or_junk_is_unclear(bad):
    """`label_fn` nổ hay trả nhãn lạ → "unclear": không bao giờ suy ra approve."""
    assert await decide("ừ", PROPOSAL, QUESTION, NOW,
                        label_fn=label_fn_returning(bad)) == "unclear"
