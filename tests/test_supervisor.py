import pytest

from app.agents.booking_graph.supervisor import supervise


class FakeModel:
    """Giả `with_structured_output` (của bộ phân tích, Task 12b): trả `schema(route=reply)`, không gọi mạng.

    Nhãn ngoài `Literal` của schema thì pydantic ném ValidationError — đúng
    điều Azure json_schema làm với output lạ, nên nhánh rơi về booking được
    test bằng chính đường lỗi thật."""

    def __init__(self, reply):
        self.reply = reply
        self.seen_messages = None
        self.schema = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return self

    async def ainvoke(self, messages, **kwargs):
        self.seen_messages = messages
        if self.schema is None:
            raise AssertionError("supervisor phải gọi with_structured_output trước ainvoke")
        return self.schema(route=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply):
        model = FakeModel(reply)
        monkeypatch.setattr(
            "app.agents.booking_graph.analyzer.build_chat_model", lambda **kw: model
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
async def test_supervisor_routes_through_structured_output(patch_model):
    """Lệch LangChain #1: nhãn đi qua `with_structured_output(Literal[...])`,
    không parse chữ tự do. Schema chỉ nhận đúng ba nhãn sống."""
    import typing
    model = patch_model("shop")
    assert (await supervise(a_state()))["route"] == "shop"
    field = model.schema.model_fields["route"].annotation
    assert set(typing.get_args(field)) == {"booking", "shop", "social"}


@pytest.mark.asyncio
async def test_supervisor_model_does_not_stream(monkeypatch):
    """Token định tuyến không bao giờ là thứ để hiện dần: `streaming=False`
    như mọi bộ phân loại khác, không chỉ trông vào lọc tag."""
    import app.agents.booking_graph.analyzer as an
    seen = []

    def build(**kw):
        seen.append(kw)
        return FakeModel("booking")

    monkeypatch.setattr(an, "build_chat_model", build)
    await supervise(a_state())
    assert seen[0]["streaming"] is False
    assert seen[0]["tags"] == ["analyze"]
    assert seen[0]["reasoning_effort"] == "low"


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

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        import asyncio
        self.calls += 1
        await asyncio.sleep(3600)


class FailingModel:
    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        raise RuntimeError("azure 500")


@pytest.mark.asyncio
async def test_supervisor_stall_falls_back_to_booking(monkeypatch):
    """Supervisor treo thì KHÔNG được ăn hết trần lượt: hết ngân sách riêng
    (wait_for) là rơi về booking — nhãn hoà đã ghi trong prompt."""
    import asyncio
    import app.agents.booking_graph.analyzer as an
    model = HangingModel()
    monkeypatch.setattr(an, "build_chat_model", lambda **kw: model)
    monkeypatch.setattr(an, "ANALYZE_TIMEOUT_SECONDS", 0.1)
    logged = []
    monkeypatch.setattr(an.logger, "warning", lambda msg, **kw: logged.append(msg))

    started = asyncio.get_running_loop().time()
    out = await supervise(a_state())

    assert out["route"] == "booking"
    assert asyncio.get_running_loop().time() - started < 2
    assert logged == ["analyze_failed"]


@pytest.mark.asyncio
async def test_supervisor_error_falls_back_to_booking(monkeypatch):
    import app.agents.booking_graph.analyzer as an
    monkeypatch.setattr(an, "build_chat_model", lambda **kw: FailingModel())
    logged = []
    monkeypatch.setattr(an.logger, "warning", lambda msg, **kw: logged.append(msg))
    assert (await supervise(a_state()))["route"] == "booking"
    assert logged == ["analyze_failed"]


@pytest.mark.asyncio
async def test_supervisor_model_has_its_own_short_budget(monkeypatch):
    """Tối đa 1 lần thử lại của SDK, timeout mỗi lần = ngân sách cả bước."""
    import app.agents.booking_graph.analyzer as an
    seen = []

    def build(**kw):
        seen.append(kw)
        return FakeModel("booking")

    monkeypatch.setattr(an, "build_chat_model", build)
    await supervise(a_state())
    assert seen[0]["max_retries"] == an.ANALYZE_MAX_RETRIES == 1
    assert seen[0]["timeout"] == an.ANALYZE_TIMEOUT_SECONDS == 8


@pytest.mark.asyncio
async def test_short_ok_after_a_booking_turn_routes_to_booking_without_llm(monkeypatch):
    """Lỗi LLM-02: agent hỏi "giữ 2 giờ chiều được không?" KHÔNG qua đề xuất
    → lượt sau không "đang chờ", vào thẳng supervisor. "Ok em" phải về booking
    mà không cần hỏi model (model giả không được gọi)."""
    calls = []
    monkeypatch.setattr(
        "app.agents.booking_graph.analyzer.build_chat_model",
        lambda **kw: calls.append(kw) or FakeModel("social"))
    out = await supervise(a_state("Ok em", last_route="booking"))
    assert out["route"] == "booking"
    assert calls == []


@pytest.mark.asyncio
async def test_short_ok_after_social_turn_still_asks_the_model(patch_model):
    """`last_route` khác "booking" thì "Ok em" vẫn đi qua model như trước."""
    model = patch_model("social")
    assert (await supervise(a_state("Ok em", last_route="social")))["route"] == "social"
    assert model.seen_messages is not None


# --- Task 12b: supervisor định tuyến bằng bộ phân tích --------------------------

@pytest.fixture
def analyzer_spy(monkeypatch):
    """Thay `analyzer.analyze_turn`: đếm lời gọi, ghi đối số, trả nhãn đặt sẵn."""
    from app.agents.booking_graph import analyzer
    spy = {"calls": [], "route": "shop"}

    async def fake(messages, customer_text, pending, bot_question=""):
        spy["calls"].append({"messages": list(messages), "customer_text": customer_text,
                             "pending": pending, "bot_question": bot_question})
        return analyzer.TurnAnalysis(route=spy["route"], stated=["muốn làm móng gel"])

    monkeypatch.setattr(analyzer, "analyze_turn", fake)
    return spy


@pytest.mark.asyncio
async def test_new_message_calls_analyzer_once_and_routes_by_it(analyzer_spy):
    from langchain_core.messages import AIMessage, HumanMessage
    state = a_state()
    state["messages"] = [HumanMessage("chào em"), AIMessage("Dạ chị cần gì ạ?"),
                         HumanMessage("tiệm mở cửa mấy giờ")]
    out = await supervise(state)

    assert out["route"] == "shop"
    assert len(analyzer_spy["calls"]) == 1
    call = analyzer_spy["calls"][0]
    assert call["pending"] is None
    assert call["customer_text"] == "tiệm mở cửa mấy giờ"
    assert call["bot_question"] == "Dạ chị cần gì ạ?"
    # Câu khách đi riêng (analyze_turn tự gói vào HumanMessage cuối) — không lặp.
    assert [m.content for m in call["messages"]] == ["chào em", "Dạ chị cần gì ạ?"]
    assert out["analysis"]["route"] == "shop"
    assert out["analysis"]["stated"] == ["muốn làm móng gel"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,text", [
    ({"queued": [{"start_at": "2099-01-01T09:00:00+07:00", "note": "cắt tóc cho cháu"}]}, "ừ"),
    ({}, "để chị hỏi lại thằng cháu rồi nhắn em"),
    ({"last_route": "booking"}, "Ok em"),
])
async def test_code_shortcuts_skip_the_analyzer(analyzer_spy, extra, text):
    """Hàng chờ, người nhà/hoãn, "ok" ngắn sau booking: code quyết, 0 lời gọi."""
    out = await supervise(a_state(text, **extra))
    assert out == {"route": "booking"}
    assert analyzer_spy["calls"] == []


@pytest.mark.asyncio
async def test_supervisor_reuses_an_analysis_made_earlier_this_turn(analyzer_spy):
    """Confirm đã phân tích câu này (đề xuất hết hiệu lực giữa chừng → về
    supervisor): KHÔNG gọi lần hai — mỗi lượt tối đa một lời gọi."""
    out = await supervise(a_state("thôi để mai", analysis={"route": "social"}))
    assert out["route"] == "social"
    assert analyzer_spy["calls"] == []
