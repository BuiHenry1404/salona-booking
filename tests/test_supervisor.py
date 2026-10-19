import pytest

from app.agents.booking_graph.supervisor import supervise


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
            **extra}


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



# --- ngân sách riêng của supervisor (review M4 I-1, lỗi live 1) ---------------

class HangingModel:
    """Azure treo: không bao giờ trả lời."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        import asyncio
        self.calls += 1
        await asyncio.sleep(3600)


class FailingModel:
    async def ainvoke(self, messages, **kwargs):
        raise RuntimeError("azure 500")


@pytest.mark.asyncio
async def test_supervisor_stall_falls_back_to_booking(monkeypatch):
    """Supervisor treo thì KHÔNG được ăn hết trần lượt: hết ngân sách riêng
    (wait_for) là rơi về booking — nhãn hoà đã ghi trong prompt."""
    import asyncio
    import app.agents.booking_graph.supervisor as sup
    model = HangingModel()
    monkeypatch.setattr(sup, "build_chat_model", lambda **kw: model)
    monkeypatch.setattr(sup, "SUPERVISOR_TIMEOUT_SECONDS", 0.1)
    logged = []
    monkeypatch.setattr(sup.logger, "warning", lambda msg, **kw: logged.append(msg))

    started = asyncio.get_running_loop().time()
    out = await supervise(a_state())

    assert out == {"route": "booking"}
    assert asyncio.get_running_loop().time() - started < 2
    assert logged == ["supervisor_failed"]


@pytest.mark.asyncio
async def test_supervisor_error_falls_back_to_booking(monkeypatch):
    import app.agents.booking_graph.supervisor as sup
    monkeypatch.setattr(sup, "build_chat_model", lambda **kw: FailingModel())
    logged = []
    monkeypatch.setattr(sup.logger, "warning", lambda msg, **kw: logged.append(msg))
    assert await supervise(a_state()) == {"route": "booking"}
    assert logged == ["supervisor_failed"]


@pytest.mark.asyncio
async def test_supervisor_model_has_its_own_short_budget(monkeypatch):
    """Tối đa 1 lần thử lại của SDK, timeout mỗi lần = ngân sách cả bước."""
    import app.agents.booking_graph.supervisor as sup
    seen = []

    def build(**kw):
        seen.append(kw)
        return FakeModel("booking")

    monkeypatch.setattr(sup, "build_chat_model", build)
    await supervise(a_state())
    assert seen[0]["max_retries"] == sup.SUPERVISOR_MAX_RETRIES == 1
    assert seen[0]["timeout"] == sup.SUPERVISOR_TIMEOUT_SECONDS == 8
