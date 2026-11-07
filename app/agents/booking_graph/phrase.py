"""Câu chốt lịch do LLM viết theo mạch, số liệu do code cấp. Lỗi → fallback."""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.context import LAST_REPLY_MAX
from app.agents.booking_graph.guard import PHRASE_TIMEOUT_SECONDS, repeats  # noqa: F401
from app.agents.booking.phrase import _TEMPLATES, code_phrase  # noqa: F401 (dời Task 6.1)
from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.prompts import (PHRASE_CONTEXT_RULES, PHRASE_KIND_SENTENCES,
                                              PHRASE_LAST_REPLY_RULE, PHRASE_PROMPT,
                                              PHRASE_QUEUED_RULE)
from app.agents.llm import build_chat_model
from app.core.logging import get_logger
from app.core.text import single_line

logger = get_logger(__name__)


async def write_phrase(state) -> str:
    """Câu chốt lịch LLM viết (tag `respond` — stream như mọi câu trả lời).

    Model lỗi, quá `PHRASE_TIMEOUT_SECONDS` hay trả rỗng → `fallback` (câu cứng
    của confirm). Guard kiểm lại số liệu sau đó (`GuardMiddleware`).
    """
    fact = state.get("confirm_fact") or {}
    fallback = state.get("fallback") or ""
    kind_sentence = PHRASE_KIND_SENTENCES.get(fact.get("kind"), "").format(error=fact.get("error") or "")
    when = fact.get("when")
    # Đặt lịch thất bại thì không có `when` — không được nhét placeholder
    # tiếng Anh vào chỗ này, nó có thể rò vào câu trả lời tiếng Việt.
    when_rule = (f"You MUST include this exact text for the date and time:\n{when}"
                 if when else "")
    queued = fact.get("queued_when") or []
    queued_rule = PHRASE_QUEUED_RULE.format(when="; ".join(queued)) if queued else ""
    # Task 15: mạch câu chốt + câu đáp gần nhất, đều nhét vào SYSTEM (không
    # thêm message mới) — giữ nguyên thứ tự messages mà guard/test đang dựa vào.
    context_rule = PHRASE_CONTEXT_RULES.get(fact.get("context") or "first_time", "")
    previous_replies = state.get("previous_replies") or []
    # Task 15 review: cắt một dòng + giới hạn độ dài NHƯ dòng "Câu em vừa trả
    # lời ở lượt trước" trong khối bối cảnh (`context.py`, cùng «» và cùng
    # `LAST_REPLY_MAX`) — câu đáp cũ có thể mang xuống dòng, không được chèn
    # nguyên văn vào SYSTEM.
    last_reply = single_line(previous_replies[-1], LAST_REPLY_MAX) if previous_replies else ""
    if last_reply:
        context_rule = f"{context_rule}{PHRASE_LAST_REPLY_RULE.format(last_reply=last_reply)}"
    system = PHRASE_PROMPT.format(kind_sentence=kind_sentence, context_rule=context_rule,
                                  when_rule=when_rule, queued_rule=queued_rule,
                                  address=fact.get("address") or "anh chị")
    # Lịch sử cắt đúng như agent (tin đã nén không gửi lại) — xem history.py.
    history = model_history(state["messages"][:-1], after=state.get("history_after"),
                            now=state.get("asked_at"))
    question = state["messages"][-1:]
    # ConversationState đứng NGAY SAU system, cùng vị trí với MessageLayoutMiddleware — không thì
    # đúng lượt chốt lịch lại là lượt duy nhất "quên" cả mạch chuyện đã
    # tóm tắt.
    # `summary` là chuỗi đã dựng sẵn (digest 5 mục hay fallback — Task 12c),
    # giống hệt khối `MessageLayoutMiddleware` gửi cho agent booking.
    summary = state.get("summary") or ""
    summary_messages = [HumanMessage(content=summary)] if summary else []
    messages = [SystemMessage(content=system), *summary_messages, *history,
                HumanMessage(content=state.get("context_block", "")), *question]
    model = build_chat_model(tags=["respond"], temperature=0.3)
    try:
        reply = await asyncio.wait_for(model.ainvoke(messages), timeout=PHRASE_TIMEOUT_SECONDS)
        draft = (reply.content or "").strip() or fallback
    except Exception as exc:
        logger.warning("phrase_failed", extra={"error": str(exc)})
        draft = fallback
    return draft
