import pytest

from app.agents.booking_graph.supervisor import route_from_state, supervise


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
async def test_small_talk_routes_to_social(patch_model):
    patch_model("social")
    assert (await supervise(a_state("chào em")))["route"] == "social"


@pytest.mark.asyncio
async def test_shop_question_routes_to_shop(patch_model):
    patch_model("shop")
    assert (await supervise(a_state("tiệm mở cửa mấy giờ")))["route"] == "shop"


@pytest.mark.asyncio
async def test_the_retired_labels_are_no_longer_valid(patch_model):
    """`status` và `refuse` đã chết. Nếu model lỡ trả nhãn cũ mà ta coi là
    hợp lệ, LangGraph sẽ nổ lúc chạy vì không có node tên đó — phải rơi về
    booking như mọi nhãn lạ khác."""
    for retired in ("status", "refuse"):
        patch_model(retired)
        assert (await supervise(a_state("gì đó")))["route"] == "booking"


def test_refuse_node_is_gone():
    import app.agents.booking_graph.supervisor as sup
    assert not hasattr(sup, "refuse")


@pytest.mark.asyncio
async def test_unrecognised_reply_falls_back_to_booking(patch_model):
    """Thà cho vào luồng đặt lịch còn hơn từ chối nhầm khách hợp lệ."""
    patch_model("tôi không chắc lắm")
    assert (await supervise(a_state()))["route"] == "booking"


@pytest.mark.asyncio
async def test_model_reply_is_case_and_space_insensitive(patch_model):
    patch_model("  SHOP \n")
    assert (await supervise(a_state()))["route"] == "shop"


@pytest.mark.asyncio
async def test_supervisor_is_not_given_the_tool_schemas(patch_model):
    """Supervisor chỉ phân loại ý định — cấp tool cho nó là lãng phí token."""
    model = patch_model("booking")
    await supervise(a_state())
    assert not hasattr(model, "bound_tools")


def test_pending_confirmation_short_circuits_the_supervisor():
    state = a_state("ừ", pending_confirmation={"start_at": "2026-08-07T08:00:00+00:00"})
    assert route_from_state(state) == "confirm"


def test_no_pending_confirmation_goes_to_supervisor():
    assert route_from_state(a_state()) == "supervisor"
