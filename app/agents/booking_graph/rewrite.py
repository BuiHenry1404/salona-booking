"""Viết lại MỘT lần câu bị guard cờ. LLM không quyết có lỗi hay không — code
đã quyết; nó chỉ sửa đúng lỗi được nêu."""
import asyncio

from langchain_core.messages import HumanMessage

from app.agents.booking_graph.guard import REWRITE_TIMEOUT_SECONDS
from app.agents.booking_graph.prompts import REWRITE_PROMPT, VIOLATION_HINTS
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

REWRITE_TAG = "rewrite"   # KHÔNG phải "respond": bản viết lại không stream, complete mới thay


def make_rewrite_node():
    async def node(state: GraphState) -> dict:
        draft = state.get("draft") or ""
        codes = state.get("violations") or []
        previous = (state.get("previous_replies") or [])[-1:] or ["(none)"]
        # Cách gọi lấy từ khối bối cảnh: dòng "Gọi khách là: ..." — tránh nhận
        # thêm User vào closure chỉ để làm một chuỗi.
        address = _address_from_block(state.get("context_block") or "")
        prompt = REWRITE_PROMPT.format(
            violations="\n".join(f"- {c}: {VIOLATION_HINTS.get(c, c)}" for c in codes),
            address=address, previous=previous[0], draft=draft,
        )
        model = build_chat_model(tags=[REWRITE_TAG], temperature=0.3, streaming=False)
        try:
            reply = await asyncio.wait_for(model.ainvoke([HumanMessage(content=prompt)]),
                                           timeout=REWRITE_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("rewrite_failed", extra={"error": str(exc), "codes": codes})
            return {"rewritten": True}
        new = (reply.content or "").strip()
        if not new:
            return {"rewritten": True}
        return {"draft": new, "original_draft": draft, "rewritten": True}

    return node


def _address_from_block(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("Gọi khách là:"):
            return line.split(":", 1)[1].strip().rstrip(".")
    return "anh chị"
