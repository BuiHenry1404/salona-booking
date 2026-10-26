"""Câu chốt lịch do LLM viết theo mạch, số liệu do code cấp. Lỗi → fallback."""
import asyncio
from typing import Any, Dict, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.guard import PHRASE_TIMEOUT_SECONDS, repeats
from app.agents.booking_graph.history import model_history
from app.agents.booking_graph.prompts import (PHRASE_KIND_SENTENCES, PHRASE_PROMPT,
                                              PHRASE_QUEUED_RULE)
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

# Câu chốt đường NÚT ("Đồng ý") — CODE viết, không gọi LLM (Task 3: lượt nút
# phải xong trong ≤1.5s). Mỗi (kind, context) có ≥3 mẫu để tránh lặp
# (`guard.repeats` so với `previous_replies`); "context" do Task 15 điền vào
# `confirm_fact`, thiếu thì coi là "first_time". `{address}`/`{when}` lấy
# nguyên văn từ fact — không đoán giới tính, không tự viết lại giờ.
_TEMPLATES: Dict[str, Dict[str, list]] = {
    "booked": {
        "first_time": [
            "Dạ, em đặt lịch cho {address} vào {when} rồi ạ.",
            "Dạ {address} ơi, lịch của {address} đã được giữ vào {when} ạ.",
            "Em xác nhận đặt xong cho {address} lúc {when} rồi nhé ạ.",
        ],
        "returning": [
            "Dạ, như mọi lần, em đặt lịch cho {address} vào {when} rồi ạ.",
            "Em giữ chỗ quen thuộc cho {address} vào {when} xong rồi ạ.",
            "Dạ {address}, lịch cũ của mình em đặt tiếp vào {when} nhé ạ.",
        ],
        "changed_time": [
            "Dạ, em đã đặt lại giờ mới cho {address} là {when} ạ.",
            "Em cập nhật xong, lịch của {address} giờ là {when} ạ.",
            "Dạ {address} ơi, giờ đặt đã đổi thành {when}, em ghi xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em đặt lịch mới cho {address} vào {when} rồi ạ.",
            "Em ghi nhận và đặt xong cho {address} vào {when} nhé ạ.",
            "Dạ {address}, lần này em đặt vào {when} xong rồi ạ.",
        ],
    },
    "moved": {
        "first_time": [
            "Dạ, em dời lịch của {address} sang {when} rồi ạ.",
            "Em chuyển lịch hẹn của {address} qua {when} xong rồi ạ.",
            "Dạ {address} ơi, lịch cũ đã đổi sang {when} rồi ạ.",
        ],
        "returning": [
            "Dạ, em dời tiếp lịch quen của {address} sang {when} rồi ạ.",
            "Em cập nhật lịch của {address} dời sang {when} xong rồi ạ.",
            "Dạ {address}, lịch mới của mình là {when}, em dời xong rồi ạ.",
        ],
        "changed_time": [
            "Dạ, em đổi giờ hẹn của {address} sang {when} rồi ạ.",
            "Em đã dời lịch, giờ mới của {address} là {when} ạ.",
            "Dạ {address} ơi, lịch được dời thành {when}, em ghi xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em dời lịch của {address} sang {when} rồi ạ.",
            "Em đã đổi giờ hẹn của {address} qua {when} xong ạ.",
            "Dạ {address}, lịch mới là {when}, em dời xong giúp {address} rồi ạ.",
        ],
    },
    "cancelled": {
        "first_time": [
            "Dạ, em huỷ lịch {when} của {address} rồi ạ.",
            "Em đã huỷ hẹn vào {when} cho {address} xong ạ.",
            "Dạ {address} ơi, lịch {when} đã được huỷ rồi ạ.",
        ],
        "returning": [
            "Dạ, lịch quen vào {when} của {address} em huỷ xong rồi ạ.",
            "Em huỷ giúp {address} lịch {when} như mọi lần nhé ạ.",
            "Dạ {address}, hẹn {when} em đã huỷ xong ạ.",
        ],
        "changed_time": [
            "Dạ, em huỷ lịch cũ {when} của {address} rồi ạ.",
            "Em đã huỷ hẹn {when}, {address} yên tâm nhé ạ.",
            "Dạ {address} ơi, lịch {when} đã huỷ xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em huỷ lịch {when} của {address} theo yêu cầu rồi ạ.",
            "Em đã huỷ hẹn {when} cho {address} xong ạ.",
            "Dạ {address}, lịch {when} em huỷ xong giúp {address} rồi ạ.",
        ],
    },
}


def code_phrase(fact: Dict[str, Any], previous_replies: Sequence[str]) -> str:
    """Câu chốt lịch đường NÚT — CODE viết, không gọi LLM.

    `kind == "failed"` dùng nguyên `fact["fallback"]` (lỗi đặt lịch, câu báo
    lỗi đã cố định sẵn, không có mẫu riêng). Các kind còn lại chọn mẫu đầu
    tiên không bị coi là lặp (`guard.repeats`) so với `previous_replies`; hết
    mẫu (mọi mẫu đều "lặp") thì dùng mẫu đầu tiên.
    """
    kind = fact.get("kind")
    if kind == "failed":
        return fact.get("fallback") or ""
    context = fact.get("context") or "first_time"
    by_context = _TEMPLATES.get(kind) or {}
    templates = by_context.get(context) or by_context.get("first_time") or []
    address = fact.get("address") or "anh chị"
    when = fact.get("when") or ""
    candidates = [t.format(address=address, when=when) for t in templates]
    for candidate in candidates:
        if not repeats(candidate, previous_replies):
            return candidate
    return candidates[0] if candidates else ""


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
    system = PHRASE_PROMPT.format(kind_sentence=kind_sentence, when_rule=when_rule,
                                  queued_rule=queued_rule,
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
