from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import (RESPOND_TAG,  # noqa: F401 (re-export)
                                             build_agent, make_agent_node)
from app.agents.booking_graph.confirm import (make_confirm_node,
                                              route_after_confirm)
from app.agents.booking_graph.guard import make_guard_node, route_after_guard
from app.agents.booking_graph.history import stamp
from app.agents.booking_graph.middleware.confirm import ConfirmMiddleware
from app.agents.booking_graph.middleware.history import HistoryWindowMiddleware
from app.agents.booking_graph.phrase import make_phrase_node
from app.agents.booking_graph.prompts import (BOOKING_PROMPT, SHOP_PROMPT,
                                              SOCIAL_PROMPT)
from app.agents.booking_graph.rewrite import make_rewrite_node
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.supervisor import route_from_state, supervise
from app.agents.booking_graph.tools import (make_booking_tools,
                                            make_shop_tools)
from app.agents.checkpoint import get_checkpointer
from app.core.clock import now_ms
from app.models.user import User


def booking_tools(db: AsyncIOMotorDatabase, user: User):
    """5 tool lịch + 2 tool đọc của shop. Câu kép ("mấy giờ đóng cửa, chiều nay
    còn giờ nào") vào booking và cần cả hai nhóm. Vẫn KHÔNG có tool ghi."""
    return make_booking_tools(db, user) + make_shop_tools(db, user)


async def record(state: GraphState) -> dict:
    """Ghi câu đáp cuối vào thread — lịch sử model của lượt sau.

    Chỉ câu đã qua guard, mang mốc `now_ms()` (run_turn ghi `conversations` bằng
    đúng mốc này). Không có câu đáp thì không ghi gì: câu khách đứng lẻ là dấu
    vết lượt hỏng, `model_history` bỏ nó như A.
    """
    answer = state.get("answer") or ""
    return {"messages": [stamp(AIMessage(content=answer), now_ms())]} if answer else {}


def build_graph(db: AsyncIOMotorDatabase, user: User, checkpointer=None):
    """Đồ thị cho một khách cụ thể.

    Dựng lại mỗi lượt vì tool đóng kín `user` trong closure — đó là cách user_id
    không bao giờ trở thành tham số mà AI có thể điều khiển.

    `checkpointer=None` → `get_checkpointer()` lúc dựng (không phải tham số mặc
    định: tham số mặc định chạy lúc import và mở MongoClient vào DB dev ngay cả
    trong test). Agent con vẫn `checkpointer=False` — thread thuộc graph ngoài.
    """
    def agent(prompt, tools, *extra):
        return make_agent_node(build_agent(
            prompt, tools, extra_middleware=[HistoryWindowMiddleware(), *extra]))

    graph = StateGraph(GraphState)

    graph.add_node("supervisor", supervise)
    graph.add_node("confirm", make_confirm_node(db, user))
    graph.add_node("shop", agent(SHOP_PROMPT, make_shop_tools(db, user)))
    # Không tool: xã giao và từ chối không cần dữ liệu gì. Vẫn dựng qua
    # build_agent để token stream ra màn hình như các node khác — trả thẳng
    # chuỗi chính là cái bẫy mà node refuse cũ mắc phải.
    graph.add_node("social", agent(SOCIAL_PROMPT, []))
    # Chỉ booking có tool đề xuất, nên chỉ booking bắt `proposal_id`.
    graph.add_node("booking", agent(BOOKING_PROMPT, booking_tools(db, user), ConfirmMiddleware()))
    graph.add_node("guard", make_guard_node(user))
    graph.add_node("rewrite", make_rewrite_node())
    graph.add_node("phrase", make_phrase_node())
    graph.add_node("record", record)

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
    # booking; nhánh đã ghi lịch sang phrase để LLM viết câu chốt theo số liệu.
    graph.add_conditional_edges(
        "confirm", route_after_confirm, {"booking": "booking", "phrase": "phrase"}
    )
    graph.add_edge("phrase", "guard")

    # Mọi câu LLM đi qua guard ĐÚNG MỘT lần trước khi ra END; rewrite tối đa
    # một lần rồi quay lại guard để kiểm, không viết lại lần hai.
    for node in ("social", "shop", "booking"):
        graph.add_edge(node, "guard")
    graph.add_conditional_edges("guard", route_after_guard, {"rewrite": "rewrite", "end": "record"})
    graph.add_edge("rewrite", "guard")
    graph.add_edge("record", END)

    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else get_checkpointer())
