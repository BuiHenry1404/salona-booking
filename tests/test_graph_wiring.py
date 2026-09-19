"""Hình dạng graph khoá cứng: mọi câu LLM qua guard đúng một lần; rewrite tối đa một lần."""
import inspect
from unittest.mock import MagicMock

from app.agents.booking_graph import confirm, guard, tools
from app.agents.booking_graph.graph import build_graph
from app.models.user import User


def edges():
    g = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan")).get_graph()
    return {(e.source, e.target) for e in g.edges}


def test_every_llm_node_feeds_the_guard():
    e = edges()
    for node in ("booking", "shop", "social"):
        assert (node, "guard") in e, node
        assert (node, "__end__") not in e, node


def test_guard_goes_to_rewrite_or_end_and_rewrite_returns_to_guard():
    e = edges()
    assert ("guard", "rewrite") in e and ("guard", "__end__") in e
    assert ("rewrite", "guard") in e
    assert ("rewrite", "__end__") not in e


def test_confirm_goes_to_phrase_then_guard_never_straight_to_end():
    e = edges()
    assert ("confirm", "phrase") in e and ("confirm", "booking") in e
    assert ("confirm", "__end__") not in e
    assert ("phrase", "guard") in e


def test_booking_node_has_the_two_shop_tools_but_no_write_tool():
    from unittest.mock import MagicMock

    from app.agents.booking_graph.graph import booking_tools
    from app.models.user import User
    names = {t.name for t in booking_tools(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))}
    assert names == {"parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
                     "cancel_appointment", "get_shop_hours", "get_shop_status"}
    assert "create_appointment" not in names


def test_no_code_path_books_from_slots():
    """Anh em với test_there_is_NO_tool_that_writes_an_appointment.

    Slots do LLM sinh. Chúng chỉ được in thành chữ trong prompt (render_slots).
    Giây phút confirm/guard/tools đọc chúng là giá trị model gõ ra đi thẳng
    vào DB — phá đúng chốt 'giá trị lấy từ DB, không từ chuỗi model gõ lại'.

    Không cấm chữ "slots" trần trụi: tools.py có find_free_slots (khung giờ
    trống trong lịch), trùng chữ nhưng không trùng khái niệm với
    ConversationSlots (bản nén hội thoại). Cấm đúng các định danh của khái
    niệm thứ hai.
    """
    forbidden = ("ConversationSlots", "sanitize_slots", "render_slots",
                 'state["slots"]', 'state.get("slots")')
    for module in (confirm, guard, tools):
        source = inspect.getsource(module)
        for needle in forbidden:
            assert needle not in source, (
                f"{module.__name__} có chuỗi '{needle}' — đọc slots hội thoại "
                f"trong code, xem CONTEXT.md bẫy #22"
            )
