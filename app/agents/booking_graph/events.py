from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List

from langchain_core.messages import AIMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.graph import RESPOND_TAG, build_graph
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.models.user import User
from app.services.conversation import ConversationService

logger = get_logger(__name__)


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


async def run_turn(
    db: AsyncIOMotorDatabase, user: User, question: str
) -> AsyncIterator[AgentEvent]:
    """Chạy một lượt chat và phát sự kiện theo thứ tự thời gian thực."""
    from app.agents.booking_graph.context import load_context

    user_id = str(user.id)
    conversations = ConversationService(db)

    yield AgentEvent("turn_started")

    try:
        context = await load_context(db, user, question)
        history = [
            HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
            for m in context["history"]
        ]

        state = {
            "messages": [*history, HumanMessage(content=question)],
            "user_id": user_id,
            "context_block": context["context_block"],
            "digest": context.get("digest", []),
            "pending_confirmation": context["pending_confirmation"],
        }

        graph = build_graph(db, user)
        # Langfuse v3: handler không mang danh tính, danh tính đi qua metadata.
        # Mỗi khách có đúng một conversation nên session_id trùng user_id.
        config = {
            "callbacks": get_callbacks(),
            "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id),
        }

        final_state: Dict[str, Any] = {}
        async for raw in graph.astream_events(state, config=config, version="v2"):
            for event in translate_langchain_event(raw):
                yield event
            if raw.get("event") == "on_chain_end" and isinstance(raw.get("data"), dict):
                output = raw["data"].get("output")
                if isinstance(output, dict) and "answer" in output:
                    final_state = output

        answer = final_state.get("answer", "")
        if answer:
            await conversations.append(user_id, "user", question)
            await conversations.append(user_id, "assistant", answer)

        yield AgentEvent("complete", {"answer": answer})

    except Exception as exc:
        logger.error("agent_turn_failed", extra={"user_id": user_id, "error": str(exc)})
        yield AgentEvent("error", {
            "message": "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."
        })
