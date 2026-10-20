"""`ConfirmMiddleware`: kết quả `propose_appointment` / `cancel_appointment` thành
`proposal_id` trong state agent — graph ngoài chỉ mang id, đề xuất nằm ở DB.

Model KHÔNG thấy `proposal_id`: middleware gỡ nó khỏi JSON tool trước khi trả
về cho model, nên đầu vào model y như A.
"""
import inspect
import json
import pathlib
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.agents.booking_graph.agents import (TOOL_ERROR_TEXT, build_agent,
                                             make_agent_node)
from app.agents.booking_graph.middleware.confirm import (ConfirmMiddleware,
                                                         proposal_id_from_tool)
from app.agents.booking_graph.tools import make_booking_tools
from app.core.clock import TZ
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from tests.fakes import scripted, tool_call


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


def by_name(tools, name):
    return next(t for t in tools if t.name == name)


def node(db, user, model):
    return make_agent_node(build_agent(
        "prompt", make_booking_tools(db, user), model=model,
        extra_middleware=[ConfirmMiddleware()]))


def a_state(text="mai 3 giờ chiều chị làm tóc"):
    return {"messages": [HumanMessage(content=text)], "context_block": "ctx"}


# --- proposal_id_from_tool (đơn vị, JSON thật sinh từ tool) -------------------

async def test_propose_result_carries_a_book_proposal(test_db):
    user = await a_user(test_db)
    content = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"})

    pid = proposal_id_from_tool("propose_appointment", content)

    doc = await test_db["proposals"].find_one({})
    assert pid == str(doc["_id"]) and doc["kind"] == "book"


async def test_replaces_makes_a_move_proposal(test_db):
    user = await a_user(test_db)
    old = await AppointmentService(test_db).create(user, tomorrow_at(9), "làm tóc")
    content = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(10).isoformat(), "replaces_appointment_id": str(old.id)})

    pid = proposal_id_from_tool("propose_appointment", content)

    doc = await test_db["proposals"].find_one({})
    assert pid == str(doc["_id"])
    assert doc["kind"] == "move" and doc["appointment_id"] == str(old.id)


async def test_cancel_result_becomes_a_cancel_proposal(test_db):
    user = await a_user(test_db)
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), None)
    content = await by_name(make_booking_tools(test_db, user), "cancel_appointment").ainvoke(
        {"appointment_id": str(appt.id)})

    pid = proposal_id_from_tool("cancel_appointment", content)

    doc = await test_db["proposals"].find_one({})
    assert pid == str(doc["_id"]) and doc["kind"] == "cancel"
    assert doc["start_at"] == appt.start_at


async def test_error_result_makes_no_proposal(test_db):
    user = await a_user(test_db)
    other = await a_user(test_db, "0938111222", "Cô Hoa")
    await AppointmentService(test_db).create(other, tomorrow_at(15), None)
    content = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat()})

    assert json.loads(content)["error"] == "slot_unavailable"
    assert proposal_id_from_tool("propose_appointment", content) is None
    assert await test_db["proposals"].count_documents({}) == 0


def test_non_json_tool_text_makes_no_proposal():
    """F8: ConfirmMiddleware đứng NGOÀI ToolFailureMiddleware — lỗi tool tới đây
    dưới dạng câu tiếng Việt, không phải JSON."""
    assert proposal_id_from_tool("propose_appointment", TOOL_ERROR_TEXT) is None
    assert proposal_id_from_tool("propose_appointment", "[1, 2]") is None
    assert proposal_id_from_tool("propose_appointment", '{"held": {}}') is None


def test_other_tools_never_make_a_proposal():
    content = json.dumps({"held": {}, "proposal_id": "a" * 24})
    assert proposal_id_from_tool("find_free_slots", content) is None


# --- qua agent thật ------------------------------------------------------------

async def test_agent_returns_the_proposal_id_from_state(test_db):
    user = await a_user(test_db)
    model = scripted([
        tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}),
        AIMessage(content="Dạ em giữ chỗ cho chị nhé, chị chốt giúp em?"),
    ])

    out = await node(test_db, user, model)(a_state())

    doc = await test_db["proposals"].find_one({})
    assert out["proposal_id"] == str(doc["_id"])
    assert out["draft"].startswith("Dạ em giữ chỗ")


async def test_the_model_never_sees_the_proposal_id(test_db):
    """JSON tool model đọc phải y như A — id là việc của code, không của model."""
    user = await a_user(test_db)
    model = scripted([
        tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"}),
        AIMessage(content="Dạ."),
    ])

    await node(test_db, user, model)(a_state())

    tool_msgs = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    seen = json.loads(tool_msgs[0].content)
    assert "proposal_id" not in seen
    assert set(seen) == {"held", "note"}


async def test_proposal_values_come_from_the_tool_result_not_model_text(test_db):
    """Model gõ giờ thiếu offset; đề xuất mang giờ tool đã chuẩn hoá (+07:00)."""
    user = await a_user(test_db)
    naive = tomorrow_at(15).replace(tzinfo=None).isoformat()
    model = scripted([tool_call("propose_appointment", {"start_at": naive}), AIMessage(content="Dạ.")])

    out = await node(test_db, user, model)(a_state())

    doc = await test_db["proposals"].find_one({})
    assert out["proposal_id"] == str(doc["_id"])
    assert doc["start_at"].astimezone(TZ) == tomorrow_at(15)


async def test_exhausted_tool_loop_drops_the_proposal(test_db):
    """G-07: giữ chỗ xong rồi vòng tool cạn — câu cuối là FALLBACK, không phải
    câu hỏi xác nhận, nên không được có đề xuất chờ duyệt."""
    user = await a_user(test_db)
    replies = [tool_call("propose_appointment", {"start_at": tomorrow_at(15).isoformat()}, id="c0")]
    replies += [tool_call("list_my_appointments", {}, id=f"c{i}") for i in range(1, 6)]

    out = await node(test_db, user, scripted(replies))(a_state())

    assert out["proposal_id"] is None


async def test_a_raising_propose_tool_makes_no_proposal_and_no_crash(test_db):
    @tool
    async def propose_appointment(start_at: str) -> str:
        """Giả lập tool nổ."""
        raise RuntimeError("mongo down")

    model = scripted([tool_call("propose_appointment", {"start_at": "x"}), AIMessage(content="Dạ.")])
    agent_node = make_agent_node(build_agent("prompt", [propose_appointment], model=model,
                                             extra_middleware=[ConfirmMiddleware()]))

    out = await agent_node(a_state())

    assert out == {"draft": "Dạ.", "proposal_id": None}
    assert [m.content for m in model.calls[1] if isinstance(m, ToolMessage)] == [TOOL_ERROR_TEXT]


async def test_an_agent_without_the_middleware_returns_no_proposal():
    model = scripted([AIMessage(content="Dạ chào chị.")])
    out = await make_agent_node(build_agent("prompt", [], model=model))(a_state("chào em"))
    assert out == {"draft": "Dạ chào chị.", "proposal_id": None}


# --- hàng rào ------------------------------------------------------------------

def test_confirm_middleware_registers_no_tools():
    assert list(ConfirmMiddleware().tools) == []


def test_confirm_middleware_keeps_no_turn_state_on_self():
    assert vars(ConfirmMiddleware()) == {}


def test_no_hitl_middleware_anywhere():
    """HITL bọc một tool GHI — tức là phải có tool ghi. Ở đây không có."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents"
    for path in root.rglob("*.py"):
        assert "HumanInTheLoopMiddleware" not in path.read_text(encoding="utf-8"), path


async def test_tools_no_longer_write_pending(test_db):
    user = await a_user(test_db)
    await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat()})
    doc = await test_db["conversations"].find_one({"user_id": str(user.id)})
    assert doc is None or not doc.get("pending_confirmation")


def test_booking_graph_wires_the_confirm_middleware_into_booking_only():
    from app.agents.booking_graph import graph
    source = inspect.getsource(graph.build_graph)
    assert "ConfirmMiddleware()" in source
