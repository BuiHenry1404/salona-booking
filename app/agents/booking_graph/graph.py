from langgraph.graph import END, START, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import make_subagent_node
from app.agents.booking_graph.confirm import (make_confirm_node,
                                              route_after_confirm)
from app.agents.booking_graph.prompts import (BOOKING_PROMPT, SHOP_PROMPT,
                                              SOCIAL_PROMPT)
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.supervisor import route_from_state, supervise
from app.agents.booking_graph.tools import (make_booking_tools,
                                            make_shop_tools)
from app.models.user import User

RESPOND_TAG = "respond"


def build_graph(db: AsyncIOMotorDatabase, user: User):
    """Đồ thị cho một khách cụ thể.

    Dựng lại mỗi lượt vì tool đóng kín `user` trong closure — đó là cách user_id
    không bao giờ trở thành tham số mà AI có thể điều khiển.
    """
    graph = StateGraph(GraphState)

    graph.add_node("supervisor", supervise)
    graph.add_node("confirm", make_confirm_node(db, user))
    graph.add_node(
        "shop",
        make_subagent_node(SHOP_PROMPT, make_shop_tools(db, user), tag=RESPOND_TAG),
    )
    # Không tool: xã giao và từ chối không cần dữ liệu gì. Vẫn dựng qua
    # make_subagent_node để token stream ra màn hình như các node khác —
    # trả thẳng chuỗi chính là cái bẫy mà node refuse cũ mắc phải.
    graph.add_node(
        "social", make_subagent_node(SOCIAL_PROMPT, [], tag=RESPOND_TAG)
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
        {"booking": "booking", "shop": "shop", "social": "social"},
    )

    # Nhánh chưa-đồng-ý của confirm không tự trả lời mà chuyển tiếp sang
    # booking — chỉ nhánh đã ghi lịch mới đi thẳng ra END.
    graph.add_conditional_edges(
        "confirm", route_after_confirm, {"booking": "booking", "end": END}
    )

    for node in ("social", "shop", "booking"):
        graph.add_edge(node, END)

    return graph.compile()
