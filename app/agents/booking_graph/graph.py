from langgraph.graph import END, START, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import make_subagent_node
from app.agents.booking_graph.confirm import make_confirm_node
from app.agents.booking_graph.prompts import BOOKING_PROMPT, STATUS_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.supervisor import (refuse, route_from_state,
                                                 supervise)
from app.agents.booking_graph.tools import (make_booking_tools,
                                            make_status_tools)
from app.models.user import User

RESPOND_TAG = "respond"


def build_graph(db: AsyncIOMotorDatabase, user: User):
    """Đồ thị cho một khách cụ thể.

    Dựng lại mỗi lượt vì tool đóng kín `user` trong closure — đó là cách user_id
    không bao giờ trở thành tham số mà AI có thể điều khiển.
    """
    graph = StateGraph(GraphState)

    graph.add_node("supervisor", supervise)
    graph.add_node("refuse", refuse)
    graph.add_node("confirm", make_confirm_node(db, user))
    graph.add_node(
        "status",
        make_subagent_node(STATUS_PROMPT, make_status_tools(db, user), tag=RESPOND_TAG),
    )
    graph.add_node(
        "booking",
        make_subagent_node(BOOKING_PROMPT, make_booking_tools(db, user), tag=RESPOND_TAG),
    )

    # Nhánh tắt: có pending_confirmation thì bỏ qua supervisor hoàn toàn.
    graph.add_conditional_edges(
        START, route_from_state, {"confirm": "confirm", "supervisor": "supervisor"}
    )
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["route"],
        {"booking": "booking", "status": "status", "refuse": "refuse"},
    )

    for node in ("confirm", "refuse", "status", "booking"):
        graph.add_edge(node, END)

    return graph.compile()
