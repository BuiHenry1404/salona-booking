from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import (RESPOND_TAG,  # noqa: F401 (re-export)
                                             build_agent, make_agent_node)
from app.agents.booking_graph.confirm import (make_confirm_node,
                                              route_after_confirm)
from app.agents.booking_graph.history import stamp
from app.agents.booking_graph.middleware.confirm import ConfirmMiddleware
from app.agents.booking_graph.middleware.history import HistoryWindowMiddleware
from app.agents.booking_graph.prompts import (BOOKING_PROMPT, SHOP_PROMPT,
                                              SOCIAL_PROMPT)
from app.agents.booking_graph.state import GraphState
from app.agents.booking_graph.supervisor import supervise
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

    Chỉ câu đã qua guard (GuardMiddleware trong agent), mang mốc `now_ms()`
    (run_turn ghi `conversations` bằng đúng mốc này). Không có câu đáp thì không ghi gì: câu khách đứng lẻ là dấu
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
        # `user` → mỗi agent có PhraseMiddleware + GuardMiddleware: câu nào ra
        # khỏi agent cũng đã qua guard đúng một lần (rewrite tối đa một lần).
        return make_agent_node(build_agent(
            prompt, tools, user=user, extra_middleware=[HistoryWindowMiddleware(), *extra]))

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
    graph.add_node("record", record)

    # Không còn nhánh tắt START → confirm: câu trả lời xác nhận tới bằng
    # resume (`Command(resume=...)`) vào ĐÚNG node confirm đang chờ; input mới
    # luôn đi từ supervisor.
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["route"],
        {"booking": "booking", "shop": "shop", "social": "social"},
    )

    # Mọi agent → record → confirm. Guard/rewrite/câu chốt nằm TRONG agent
    # (middleware), nên câu hỏi xác nhận đã qua guard và nằm trong thread
    # TRƯỚC khi confirm interrupt.
    for node in ("social", "shop", "booking"):
        graph.add_edge(node, "record")
    graph.add_edge("record", "confirm")

    # confirm:
    # - end: không có đề xuất chờ;
    # - phrase: đã ghi (approve) hoặc hỏi lại MỘT câu (clarify) → agent booking
    #   mang `confirm_fact`; PhraseMiddleware viết câu thay cho model, Guard kiểm;
    # - booking: khách từ chối/đổi ý → booking trả lời như tin mới;
    # - supervisor: đề xuất hết hạn/không còn pending → tin mới từ đầu.
    graph.add_conditional_edges(
        "confirm", route_after_confirm,
        {"end": END, "phrase": "booking", "booking": "booking", "supervisor": "supervisor"},
    )

    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else get_checkpointer())
