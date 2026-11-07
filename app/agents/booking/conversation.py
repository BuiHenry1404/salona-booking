"""`render_brief`/`respond` — Conversation Agent (Task 4.2, §8.3 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Agent DUY NHẤT nói với khách và stream (`RESPOND_TAG`, AGENTS.md: chỉ token
gắn nhãn `respond` được đẩy ra màn hình). Nó không gọi tool, không ghi DB,
chỉ nhận `ReplyBrief` (Task 4.1) + cửa sổ lịch sử (`history.model_history`,
gói cũ) + câu khách của lượt này, rồi trả một câu trả lời cho khách.

`render_brief` chỉ DỰNG CHỮ từ các trường của `ReplyBrief` — không bịa, mọi
id/iso/spoken in ra đều lấy từ `brief`. Câu khách KHÔNG nằm trong khối này:
nó chỉ xuất hiện đúng một lần, sau nhãn `UNTRUSTED`, trong `HumanMessage`
cuối cùng (như `specialists/base.py::task_message` và
`orchestrator/agent.py::orchestrator_message`)."""
from __future__ import annotations

import asyncio

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.booking.context import UNTRUSTED
from app.agents.booking.prompts.conversation import CONVERSATION_PROMPT
from app.agents.booking.schemas import ClarifyRequest, Fact, ReplyBrief
from app.agents.booking.middleware import is_transient
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

# AGENTS.md (ràng buộc cứng): chỉ model này gắn nhãn "respond" và stream —
# đây là token DUY NHẤT được đẩy ra màn hình khách.
RESPOND_TAG = "respond"

# Trần thời gian một lần gọi model trả lời khách (riêng của agent này, không
# dùng chung `LLM_TIMEOUT_SECONDS` của `llm.py` vì đây là lượt khách đang chờ
# chữ hiện dần, không phải lời gọi JSON nền).
RESPOND_TIMEOUT_SECONDS = 20

# Số lần thử CHẠY LẠI khi lỗi tạm thời (`is_transient`) — đúng MỘT lần thử
# lại, cùng quy ước `MAX_RUN_ATTEMPTS` của `specialists/base.py`.
_MAX_ATTEMPTS = 2


def _render_fact(fact: Fact) -> str:
    parts = [fact.key]
    if fact.spoken is not None:
        parts.append(f"spoken={fact.spoken}")
    if fact.iso is not None:
        parts.append(f"iso={fact.iso}")
    if fact.text is not None:
        parts.append(f"text={fact.text}")
    return " ".join(parts)


def _render_ask(ask: ClarifyRequest | None) -> str:
    if ask is None:
        return "Ask: none"
    line = f"Ask: field={ask.field}"
    if ask.hint:
        line += f" hint={ask.hint}"
    return line


def render_brief(brief: ReplyBrief) -> str:
    """Khối nhãn tiếng Anh Conversation Agent đọc — mỗi trường của
    `ReplyBrief` ra đúng một nhãn, không trộn chữ khách vào đây (xem
    docstring module)."""
    lines: list[str] = [f"Kind: {brief.kind}", f"Address: {brief.address}"]

    if brief.must_say:
        lines.append("Say exactly:")
        lines.extend(f"- {s}" for s in brief.must_say)
    else:
        lines.append("Say exactly: none")

    lines.append("Facts:")
    lines.extend(f"- {_render_fact(f)}" for f in brief.facts)

    lines.append(_render_ask(brief.ask))

    if brief.must_not_claim:
        lines.append("Never claim: " + ", ".join(brief.must_not_claim))
    else:
        lines.append("Never claim: none")

    lines.append(f"Then: {brief.then}" if brief.then else "Then: none")
    lines.append(
        f"Previous reply: {brief.previous_reply}" if brief.previous_reply else "Previous reply: none"
    )
    lines.append(f"Tone: {brief.tone_context}")

    if brief.staff_note:
        lines.append(f"Staff note: {brief.staff_note}")
    if brief.digest_text:
        lines.append(f"Digest: {brief.digest_text}")

    lines.append(f"Now: {brief.now_local.isoformat()}")

    return "\n".join(lines)


async def respond(
    brief: ReplyBrief, history: list[BaseMessage], question: str, *, log_extra: dict | None = None
) -> tuple[str, str]:
    """Gọi Conversation Agent cho một lượt. Trả `(draft, source)` — `source`
    là `"llm"` khi model trả chữ, `"code"` khi lỗi/timeout/rỗng buộc dùng
    `brief.fallback` (câu chốt CODE viết sẵn, không phải câu chung).

    Lỗi tạm thời (`is_transient` — timeout tính là tạm thời, xem
    `app.agents.booking.middleware.is_transient`) được thử lại ĐÚNG MỘT
    lần; lỗi khác, hoặc lỗi của lần thử lại, trả fallback ngay. Không bao giờ
    nuốt lỗi lặng lẽ — luôn log một sự kiện tên ổn định trước khi trả
    fallback (không log chữ khách, §11). `log_extra` (graph truyền
    `user_id`/`turn_id`/`thread_id`) gộp vào `extra` của hai dòng log đó."""
    ids = log_extra or {}
    message = HumanMessage(content=f"{render_brief(brief)}\n\n{UNTRUSTED}{question}")
    messages: list[BaseMessage] = [SystemMessage(content=CONVERSATION_PROMPT), *history, message]

    model = build_chat_model(tags=[RESPOND_TAG], streaming=True, max_retries=0)

    attempts = 0
    while True:
        attempts += 1
        try:
            result = await asyncio.wait_for(model.ainvoke(messages), RESPOND_TIMEOUT_SECONDS)
        except Exception as exc:
            if attempts < _MAX_ATTEMPTS and is_transient(exc):
                continue
            logger.warning(
                "respond_failed",
                extra={**ids, "reason": type(exc).__name__, "attempts": attempts, "kind": brief.kind},
            )
            return brief.fallback, "code"

        text = result.content if isinstance(result.content, str) else str(result.content)
        if not text.strip():
            logger.warning("respond_empty", extra={**ids, "attempts": attempts, "kind": brief.kind})
            return brief.fallback, "code"
        return text, "llm"
