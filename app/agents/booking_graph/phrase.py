"""Câu chốt lịch do LLM viết theo mạch, số liệu do code cấp. Lỗi → fallback."""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.agents import DIGEST_HEADER
from app.agents.booking_graph.guard import PHRASE_TIMEOUT_SECONDS
from app.agents.booking_graph.prompts import PHRASE_KIND_SENTENCES, PHRASE_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_phrase_node():
    async def node(state: GraphState) -> dict:
        fact = state.get("confirm_fact") or {}
        fallback = state.get("fallback") or ""
        kind_sentence = PHRASE_KIND_SENTENCES.get(fact.get("kind"), "").format(error=fact.get("error") or "")
        when = fact.get("when")
        # Đặt lịch thất bại thì không có `when` — không được nhét placeholder
        # tiếng Anh vào chỗ này, nó có thể rò vào câu trả lời tiếng Việt.
        when_rule = (f"You MUST include this exact text for the date and time:\n{when}"
                     if when else "")
        system = PHRASE_PROMPT.format(kind_sentence=kind_sentence, when_rule=when_rule,
                                      address=fact.get("address") or "anh chị")
        history, question = state["messages"][:-1], state["messages"][-1:]
        # Digest đứng NGAY SAU system, cùng vị trí với agents.py — không thì
        # đúng lượt chốt lịch lại là lượt duy nhất "quên" cả mạch chuyện đã
        # tóm tắt.
        bullets = state.get("digest") or []
        digest_messages = (
            [HumanMessage(content=DIGEST_HEADER + "\n" + "\n".join(f"- {b}" for b in bullets))]
            if bullets else []
        )
        messages = [SystemMessage(content=system), *digest_messages, *history,
                    HumanMessage(content=state.get("context_block", "")), *question]
        model = build_chat_model(tags=["respond"], temperature=0.3)
        try:
            reply = await asyncio.wait_for(model.ainvoke(messages), timeout=PHRASE_TIMEOUT_SECONDS)
            draft = (reply.content or "").strip() or fallback
        except Exception as exc:
            logger.warning("phrase_failed", extra={"error": str(exc)})
            draft = fallback
        return {"draft": draft, "phrase_fact": fact}

    return node
