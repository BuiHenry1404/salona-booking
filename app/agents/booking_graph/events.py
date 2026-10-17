import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import RESPOND_TAG
from app.agents.booking_graph.graph import build_graph
from app.agents.booking_graph.history import stamp, stamped_at
from app.core.clock import now_ms, to_local
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.models.user import User
from app.services.conversation import ConversationService, session_cutoff
from app.services.conversation_state import ConversationStateService

logger = get_logger(__name__)


# Giữ tham chiếu mạnh: event loop chỉ giữ weak-ref, tác vụ nền không ai giữ
# có thể bị GC giữa chừng — mất lượt nén mà không có dòng log nào.
_PENDING_COMPACTIONS: set = set()


def schedule_compaction(db: AsyncIOMotorDatabase, user_id: str) -> None:
    """Nén nền, không await: khách đã nhận complete. maybe_compact tự nuốt lỗi."""
    task = asyncio.create_task(ConversationStateService(db).maybe_compact(user_id))
    _PENDING_COMPACTIONS.add(task)
    task.add_done_callback(_PENDING_COMPACTIONS.discard)


@dataclass
class AgentEvent:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


def translate_langchain_event(event: dict) -> List[AgentEvent]:
    """Dịch sự kiện thô của LangGraph sang sự kiện gửi cho giao diện.

    Điểm quan trọng nhất: CHỈ chuyển tiếp token mang tag "respond". Không lọc thì
    token JSON định tuyến của supervisor sẽ chạy ngang màn hình khách.
    """
    kind = event.get("event")

    if kind == "on_chat_model_stream":
        if RESPOND_TAG not in (event.get("tags") or []):
            return []
        chunk = (event.get("data") or {}).get("chunk")
        text = getattr(chunk, "content", "") or ""
        return [AgentEvent("token", {"text": text})] if text else []

    if kind == "on_tool_start":
        return [AgentEvent("tool_started", {"name": event.get("name", "")})]

    if kind == "on_tool_end":
        return [AgentEvent("tool_finished", {"name": event.get("name", ""), "ok": True})]

    if kind == "on_tool_error":
        return [AgentEvent("tool_finished", {"name": event.get("name", ""), "ok": False})]

    return []


def thread_id_for(user_id: str, now: datetime) -> str:
    """Một thread mỗi khách mỗi NGÀY VN — cùng ranh giới phiên với A.

    Theo giờ VN, không theo UTC: nửa đêm UTC là 7 giờ sáng ở VN, cắt giữa buổi.
    """
    return f"{user_id}:{to_local(now).date().isoformat()}"


async def _last_checkpoint_at(graph, thread_id: str) -> Optional[datetime]:
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    created_at = getattr(snap, "created_at", None)
    return datetime.fromisoformat(created_at) if created_at else None


async def pick_thread(graph, user_id: str, now: datetime) -> str:
    """Thread của lượt này: hôm nay, trừ ca nối qua nửa đêm.

    Luật 30 phút của A (khách được hỏi 23:58, đáp "ừ" 00:01) cần tin hôm qua.
    Thread hôm nay CHƯA có checkpoint mà thread hôm qua có checkpoint mới hơn
    mốc cắt phiên (`session_cutoff`: đầu ngày, hoặc 30 phút trước nếu sớm hơn)
    thì dùng tiếp thread hôm qua. So với mốc cắt phiên chứ không chỉ "30 phút
    gần nhất": khách nối lúc 00:10 rồi nhắn lúc 00:40 thì A vẫn giữ tin 00:10,
    nên thread cũng phải giữ. Cửa sổ model vẫn do `model_history` cắt.
    """
    today = thread_id_for(user_id, now)
    if await _last_checkpoint_at(graph, today) is not None:
        return today
    yesterday = thread_id_for(user_id, to_local(now) - timedelta(days=1))
    last = await _last_checkpoint_at(graph, yesterday)
    if last is not None and last >= session_cutoff(now):
        return yesterday
    return today


def fresh_turn_fields(context: dict, question: str) -> dict:
    """Mọi khoá một lượt sinh ra, đặt lại cho lượt mới.

    Thread checkpointer GIỮ state lượt trước: khoá nào không có trong input là
    mang giá trị cũ sang — `answer` cũ làm `route_after_guard` bỏ qua guard,
    `pending_confirmation` cũ (đã dùng hoặc đã quá 10 phút) đưa START thẳng vào
    confirm và ghi lịch lần hai. Pending vì vậy đọc lại từ Mongo MỖI lượt.
    """
    return {
        "answer": "",
        "answer_source": "llm",
        "draft": "",
        "original_draft": "",
        "violations": [],
        "rewritten": False,
        "route": "",
        "fallback": None,
        "confirm_fact": None,
        "phrase_fact": None,
        "context_block": context["context_block"],
        "summary": context.get("summary") or [],
        "slots": context.get("slots"),
        "history_after": context.get("history_after"),
        "pending_confirmation": context.get("pending_confirmation"),
        "previous_replies": [m.content for m in context.get("history") or []
                             if m.role == "assistant" and m.source == "llm"],
        "customer_text": question,
        "user_id": context.get("user_id", ""),
    }


async def run_turn(
    db: AsyncIOMotorDatabase, user: User, question: str
) -> AsyncIterator[AgentEvent]:
    """Chạy một lượt chat và phát sự kiện theo thứ tự thời gian thực.

    Lịch sử model nằm trong thread checkpointer (`pick_thread`); `conversations`
    vẫn được ghi như cũ làm nhật ký hiển thị, cùng mốc thời gian với thread.
    """
    from app.agents.booking_graph.context import load_context

    user_id = str(user.id)
    conversations = ConversationService(db)

    yield AgentEvent("turn_started")

    try:
        # Một mốc cho câu khách ở CẢ HAI nơi (thread và conversations):
        # covers_until đọc từ conversations phải khớp đúng tin bên thread.
        asked_at = now_ms()
        context = await load_context(db, user, question)
        graph = build_graph(db, user)
        thread_id = await pick_thread(graph, user_id, asked_at)

        # Langfuse v3: handler không mang danh tính, danh tính đi qua metadata.
        # session_id = user_id (không phải thread_id): trace của một khách
        # vẫn gom về một chỗ dù thread đổi theo ngày.
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": get_callbacks(),
            "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id),
        }
        turn_input = {
            "messages": [stamp(HumanMessage(content=question), asked_at)],
            **fresh_turn_fields(context, question),
        }

        async for raw in graph.astream_events(turn_input, config=config, version="v2"):
            for event in translate_langchain_event(raw):
                yield event

        values = (await graph.aget_state(config)).values or {}
        answer = values.get("answer", "")
        if answer:
            last = (values.get("messages") or [None])[-1]
            answered_at = stamped_at(last) if isinstance(last, AIMessage) else None
            await conversations.append(user_id, "user", question, created_at=asked_at)
            await conversations.append(user_id, "assistant", answer,
                                       source=values.get("answer_source", "llm"),
                                       created_at=answered_at or now_ms())

        yield AgentEvent("complete", {"answer": answer})

        if answer:
            # Sau complete, ngoài đường trả lời. Lỗi lên lịch (hiếm) cũng không
            # được biến thành sự kiện error cho một lượt đã xong.
            try:
                schedule_compaction(db, user_id)
            except Exception as exc:
                logger.warning("state_schedule_failed", extra={
                    "user_id": user_id, "error": str(exc), "error_type": type(exc).__name__,
                })

    except Exception as exc:
        logger.error("agent_turn_failed", extra={"user_id": user_id, "error": str(exc)})
        yield AgentEvent("error", {
            "message": "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."
        })
