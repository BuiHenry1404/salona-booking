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
            "pending_confirmation": None, **extra}


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
