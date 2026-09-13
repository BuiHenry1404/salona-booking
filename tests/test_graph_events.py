import pytest

from app.agents.booking_graph.events import (RESPOND_TAG, AgentEvent,
                                             translate_langchain_event)


def lc_event(name, tags=None, **rest):
    return {"event": name, "tags": tags or [], "name": rest.pop("node_name", "x"), **rest}


def test_token_from_the_respond_node_is_forwarded():
    class Chunk:
        content = "Dạ"

    events = translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[RESPOND_TAG], data={"chunk": Chunk()})
    )
    assert [(e.type, e.data["text"]) for e in events] == [("token", "Dạ")]


def test_token_from_the_supervisor_is_dropped():
    """Token của supervisor là JSON định tuyến — lọt ra màn hình là rác chạy ngang."""
    class Chunk:
        content = "booking"

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=["supervisor"], data={"chunk": Chunk()})
    ) == []


def test_token_without_any_tag_is_dropped():
    class Chunk:
        content = "?"

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[], data={"chunk": Chunk()})
    ) == []


def test_empty_token_is_dropped():
    class Chunk:
        content = ""

    assert translate_langchain_event(
        lc_event("on_chat_model_stream", tags=[RESPOND_TAG], data={"chunk": Chunk()})
    ) == []


def test_tool_start_becomes_tool_started_with_the_raw_name():
    """Backend gửi tên thô; dịch sang tiếng Việt là việc của frontend."""
    events = translate_langchain_event(lc_event("on_tool_start", node_name="find_free_slots"))
    assert [(e.type, e.data["name"]) for e in events] == [("tool_started", "find_free_slots")]


def test_tool_end_becomes_tool_finished_ok():
    events = translate_langchain_event(lc_event("on_tool_end", node_name="find_free_slots"))
    assert events[0].type == "tool_finished"
    assert events[0].data == {"name": "find_free_slots", "ok": True}


def test_tool_error_becomes_tool_finished_not_ok():
    events = translate_langchain_event(lc_event("on_tool_error", node_name="propose_appointment"))
    assert events[0].data == {"name": "propose_appointment", "ok": False}


def test_unrelated_events_produce_nothing():
    assert translate_langchain_event(lc_event("on_chain_start")) == []
    assert translate_langchain_event(lc_event("on_retriever_end")) == []


def test_agent_event_is_json_serialisable():
    import json
    event = AgentEvent(type="token", data={"text": "Dạ"})
    assert json.loads(json.dumps(event.data)) == {"text": "Dạ"}


@pytest.mark.asyncio
async def test_graph_has_the_three_live_routes_and_no_refuse(test_db):
    """Nếu graph thiếu node mà supervisor trả nhãn đó, LangGraph nổ lúc chạy
    chứ không phải lúc test — nên chốt danh sách node ở đây.

    Phải truyền `test_db` thật, KHÔNG truyền None: BaseRepository.__init__
    làm `db[collection_name]` ngay lúc dựng, nên None nổ TypeError.
    """
    from app.agents.booking_graph.graph import build_graph
    from app.models.user import User

    user = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")
    nodes = set(build_graph(test_db, user).get_graph().nodes)
    for expected in ("supervisor", "booking", "shop", "social", "confirm"):
        assert expected in nodes
    assert "refuse" not in nodes


async def test_load_context_passes_the_last_assistant_reply(test_db):
    """Câu đáp cuối của lượt trước phải tới được khối bối cảnh — không thì dòng
    "không nói lại nguyên văn" không bao giờ xuất hiện trong thực tế."""
    from app.agents.booking_graph.context import load_context
    from app.services.auth import AuthService
    from app.services.conversation import ConversationService

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    convs = ConversationService(test_db)
    await convs.append(str(user.id), "user", "khách nào đặt 4 giờ")
    await convs.append(str(user.id), "assistant", "Em chỉ xem lịch của chị Lan thôi ạ.")

    context = await load_context(test_db, user, "tôi là chủ tiệm")
    assert "Em chỉ xem lịch của chị Lan thôi ạ." in context["context_block"]
