# Task 6 · Supervisor và nhánh từ chối

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/agents/booking_graph/prompts.py`, `app/agents/booking_graph/supervisor.py`, `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `build_chat_model` (task 1), `GraphState` (task 4)
- Produces:
  - `prompts.py`: `SUPERVISOR_PROMPT`, `STATUS_PROMPT`, `BOOKING_PROMPT`, `REFUSE_MESSAGE`
  - `supervisor.py`: `supervise(state: GraphState) -> dict` (đặt `route`), `refuse(state: GraphState) -> dict`, `route_from_state(state) -> str`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_supervisor.py`:

```python
import pytest

from app.agents.booking_graph.supervisor import (refuse, route_from_state,
                                                 supervise)


class FakeModel:
    """Trả về đúng một chuỗi, không gọi mạng."""

    def __init__(self, reply):
        self.reply = reply
        self.seen_messages = None

    async def ainvoke(self, messages, **kwargs):
        from langchain_core.messages import AIMessage
        self.seen_messages = messages
        return AIMessage(content=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply):
        model = FakeModel(reply)
        monkeypatch.setattr(
            "app.agents.booking_graph.supervisor.build_chat_model", lambda **kw: model
        )
        return model
    return _install


def a_state(text="mai 3h chiều làm tóc được không con", **extra):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)], "user_id": "u1",
            "context_block": "Bạn đang nói chuyện với: Cô Lan (0912345678).",
            "recalled": [], "pending_confirmation": None, **extra}


@pytest.mark.asyncio
async def test_booking_intent_routes_to_booking(patch_model):
    patch_model("booking")
    assert (await supervise(a_state()))["route"] == "booking"


@pytest.mark.asyncio
async def test_status_intent_routes_to_status(patch_model):
    patch_model("status")
    assert (await supervise(a_state("chú có rảnh giờ không")))["route"] == "status"


@pytest.mark.asyncio
async def test_off_topic_routes_to_refuse(patch_model):
    patch_model("refuse")
    assert (await supervise(a_state("cháu bán bảo hiểm không")))["route"] == "refuse"


@pytest.mark.asyncio
async def test_unrecognised_reply_falls_back_to_booking(patch_model):
    """Thà cho vào luồng đặt lịch còn hơn từ chối nhầm khách hợp lệ."""
    patch_model("tôi không chắc lắm")
    assert (await supervise(a_state()))["route"] == "booking"


@pytest.mark.asyncio
async def test_model_reply_is_case_and_space_insensitive(patch_model):
    patch_model("  STATUS \n")
    assert (await supervise(a_state()))["route"] == "status"


@pytest.mark.asyncio
async def test_supervisor_is_not_given_the_tool_schemas(patch_model):
    """Supervisor chỉ phân loại ý định — cấp tool cho nó là lãng phí token."""
    model = patch_model("booking")
    await supervise(a_state())
    assert not hasattr(model, "bound_tools")


@pytest.mark.asyncio
async def test_refuse_produces_a_polite_vietnamese_answer():
    result = await refuse(a_state("cháu bán bảo hiểm không"))
    assert "đặt lịch" in result["answer"].lower()
    assert len(result["answer"]) < 300


def test_pending_confirmation_short_circuits_the_supervisor():
    state = a_state("ừ", pending_confirmation={"start_at": "2026-08-07T08:00:00+00:00"})
    assert route_from_state(state) == "confirm"


def test_no_pending_confirmation_goes_to_supervisor():
    assert route_from_state(a_state()) == "supervisor"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_supervisor.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.supervisor'`

- [ ] **Step 3: Viết `app/agents/booking_graph/prompts.py`**

```python
SUPERVISOR_PROMPT = """Bạn phân loại ý định của khách tại một tiệm làm nail và tóc.

Trả lời DUY NHẤT một từ, không giải thích:
- booking : khách muốn đặt lịch, đổi lịch, hủy lịch, hỏi giờ trống, hoặc xem lịch của mình
- status  : khách hỏi chủ tiệm đang bận hay rảnh, hoặc khi nào xong
- refuse  : mọi thứ khác — chuyện phiếm, quảng cáo, hỏi kiến thức, nhờ làm việc khác

Khi phân vân giữa booking và refuse, chọn booking."""

STATUS_PROMPT = """Bạn là lễ tân của một tiệm làm nail và tóc, nói chuyện với khách lớn tuổi.

Dùng tool get_shop_status để biết chủ tiệm đang bận hay rảnh, rồi trả lời.

Cách nói:
- Xưng "con", gọi khách theo tên trong phần bối cảnh
- Một đến hai câu, ngắn gọn, không dùng từ kỹ thuật
- Nếu chủ tiệm đang bận, nói rõ MẤY GIỜ xong (ví dụ "xong lúc 3 giờ rưỡi chiều ạ").
  Không nói "còn 30 phút" — câu đó nằm lại trong lịch sử chat và sai ngay sau đó."""

BOOKING_PROMPT = """Bạn là lễ tân của một tiệm làm nail và tóc, nói chuyện với khách lớn tuổi.
Bạn CHỈ giúp việc đặt lịch. Không tư vấn, không trò chuyện ngoài lề.

Quy tắc bắt buộc:
1. Bạn KHÔNG có tool nào ghi lịch. Thứ tự bắt buộc:
   parse_time → find_free_slots (nếu cần) → propose_appointment → hỏi khách xác nhận.
   Sau khi propose_appointment xong, nhắc lại đầy đủ ngày, giờ và việc làm:
   "Con đặt Thứ Năm 7/8, 3 giờ chiều, làm tóc — đúng không cô?"
   Lịch chỉ được ghi khi khách trả lời đồng ý ở lượt sau. Đừng nói "đã đặt xong"
   trước lúc đó.
2. Muốn hủy lịch thì LUÔN gọi list_my_appointments trước để lấy mã lịch.
   Nếu khách có từ hai lịch trở lên, phải hỏi rõ hủy lịch nào.
3. Nếu giờ khách muốn đã có người, gợi ý hai giờ trống gần nhất.
4. Không bịa giờ trống — luôn dùng find_free_slots.
5. Khách nhắc tới thời gian ("mai", "chiều nay", "thứ Năm tuần sau") thì LUÔN
   gọi parse_time trước, rồi mới gọi find_free_slots hoặc create_appointment.
   Tuyệt đối không tự tính ngày.
   - parse_time trả start_at → chuyển NGUYÊN chuỗi đó sang propose_appointment,
     không sửa, không diễn giải, không tự gõ lại.
   - parse_time trả missing → hỏi lại khách đúng thứ còn thiếu, hỏi MỘT thứ
     một lần. Ví dụ missing là ["sáng hay chiều"] thì hỏi "Dạ 3 giờ chiều hay
     3 giờ sáng ạ cô?" — không hỏi kèm thứ khác.
   Vẫn nhắc lại ngày bằng lời cho khách nghe trước khi ghi lịch.

Cách nói: xưng "con", gọi khách theo tên trong phần bối cảnh, câu ngắn,
không dùng từ kỹ thuật, không dùng dấu đầu dòng."""

REFUSE_MESSAGE = (
    "Dạ con chỉ giúp được việc đặt lịch làm tóc và làm nail thôi ạ. "
    "Cô chú cần đặt lịch ngày nào để con xem giúp ạ?"
)
```

- [ ] **Step 4: Viết `app/agents/booking_graph/supervisor.py`**

```python
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.prompts import REFUSE_MESSAGE, SUPERVISOR_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_ROUTES = {"booking", "status", "refuse"}
SUPERVISOR_HISTORY_TURNS = 4


def route_from_state(state: GraphState) -> str:
    """Nhánh tắt: khách vừa được hỏi xác nhận thì câu trả lời đi thẳng vào thực thi.

    Tiết kiệm một lượt LLM và loại bỏ khả năng supervisor hiểu "ừ" thành ý định mới.
    """
    return "confirm" if state.get("pending_confirmation") else "supervisor"


async def supervise(state: GraphState) -> dict:
    """Phân loại ý định.

    Supervisor KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp tới —
    chỉ vài lượt chat cuối. Nó chỉ cần biết khách đang muốn gì.
    """
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    recent = state["messages"][-SUPERVISOR_HISTORY_TURNS:]

    reply = await model.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent])
    route = (reply.content or "").strip().lower()

    if route not in VALID_ROUTES:
        logger.info("supervisor_fallback", extra={"raw": route[:80]})
        route = "booking"

    return {"route": route}


async def refuse(state: GraphState) -> dict:
    """Câu ngoài chủ đề không bao giờ tới subagent — đây là lớp thứ nhất của
    ràng buộc 'AI chỉ để đặt lịch'."""
    return {"answer": REFUSE_MESSAGE}
```

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_supervisor.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/prompts.py app/agents/booking_graph/supervisor.py tests/test_supervisor.py
git commit -m "feat: intent supervisor with explicit refuse branch"
```
