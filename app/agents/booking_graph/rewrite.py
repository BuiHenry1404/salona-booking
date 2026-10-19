"""Viết lại MỘT lần câu bị guard cờ. LLM không quyết có lỗi hay không — code
đã quyết; nó chỉ sửa đúng lỗi được nêu."""
import asyncio
from typing import List, Optional, Sequence

from langchain_core.messages import HumanMessage

from app.agents.booking_graph.guard import REPEAT_LOOKBACK, REWRITE_TIMEOUT_SECONDS
from app.agents.booking_graph.prompts import REWRITE_PROMPT, VIOLATION_HINTS
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

REWRITE_TAG = "rewrite"   # KHÔNG phải "respond": bản viết lại không stream, complete mới thay


async def rewrite_once(draft: str, codes: List[str], previous_replies: Sequence[str],
                       context_block: str) -> Optional[str]:
    """Viết lại `draft` đúng MỘT lần theo các mã lỗi. None khi model lỗi, quá
    giờ hoặc trả rỗng — guard khi đó coi như rewrite không đổi được gì.

    Tag `rewrite` + `streaming=False`: bản viết lại không stream, `complete`
    mới thay câu trên màn hình (bẫy #8)."""
    # Cùng cửa sổ mà `repeats()` đã so, không phải mỗi câu cuối: guard bắt
    # lặp với câu ở vài lượt trước rồi đưa người viết lại một câu KHÁC kèm
    # lệnh "đừng lặp câu này" — nó không bao giờ thấy câu mình đã trùng.
    window = list(previous_replies or [])[-REPEAT_LOOKBACK:]
    previous = "\n".join(f"- {p}" for p in window) or "(none)"
    # Cách gọi lấy từ khối bối cảnh: dòng "Gọi khách là: ..." — tránh nhận
    # thêm User chỉ để làm một chuỗi.
    address = _address_from_block(context_block or "")
    prompt = REWRITE_PROMPT.format(
        violations="\n".join(f"- {c}: {VIOLATION_HINTS.get(c, c)}" for c in codes),
        address=address, previous=previous, draft=draft,
    )
    model = build_chat_model(tags=[REWRITE_TAG], temperature=0.3, streaming=False)
    try:
        reply = await asyncio.wait_for(model.ainvoke([HumanMessage(content=prompt)]),
                                       timeout=REWRITE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("rewrite_failed", extra={"error": str(exc), "codes": codes})
        return None
    return (reply.content or "").strip() or None


def make_rewrite_node():
    """Node rewrite của graph ngoài — chỉ còn tới Task 4.2 (GuardMiddleware)."""
    async def node(state: GraphState) -> dict:
        draft = state.get("draft") or ""
        new = await rewrite_once(draft, state.get("violations") or [],
                                 state.get("previous_replies") or [],
                                 state.get("context_block") or "")
        if not new:
            return {"rewritten": True}
        return {"draft": new, "original_draft": draft, "rewritten": True}

    return node


def _address_from_block(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("Gọi khách là:"):
            return line.split(":", 1)[1].strip().rstrip(".")
    return "anh chị"
