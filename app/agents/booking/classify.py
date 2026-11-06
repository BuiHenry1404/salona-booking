"""`classify_only` — bộ phân loại rẻ cho lượt "sạch" (R4, Task 4.4, §4/§5
của `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Resolver (`resolver.py`, R4) chỉ gọi hàm này khi lượt không có
pending/open_question/hàng chờ/việc chưa báo và câu khách không nhắc số/giờ/
ngày/thứ nào — đây là một lượt rẻ, dùng khối bối cảnh `lite` (không
`upcoming`, không id lịch hẹn) cộng câu khách, KHÔNG dùng lịch sử nhiều lượt.

`route` trả về quyết định bước tiếp theo ở `resolver`: `social`/`unsupported`
→ trả lời thẳng (Conversation Agent với `ReplyBrief.kind` tương ứng, không
chạm Orchestrator); `business` → đi tiếp vào Orchestrator như một lượt R5
bình thường. Mọi lỗi (model lỗi, parse hỏng, quá giờ) đều về `business` —
thà tốn thêm một vòng Orchestrator còn hơn bỏ sót một yêu cầu thật."""
from __future__ import annotations

import asyncio
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.booking.context import UNTRUSTED, TurnContext, render_context
from app.agents.booking.prompts.classify import CLASSIFY_PROMPT
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

# Nhãn DUY NHẤT gắn cho model này (AGENTS.md: chỉ model gắn "respond" được
# stream ra màn hình khách; model này không stream và không bao giờ mang
# nhãn đó).
CLASSIFY_TAG = "classify"

# Trần thời gian riêng của lượt gọi rẻ này (như rewrite/phrase/timeparse/
# analyze — mỗi lời gọi đơn bọc `wait_for` 8 giây của chính nó).
CLASSIFY_TIMEOUT_SECONDS = 8


class ClassifyOut(BaseModel):
    route: Literal["social", "unsupported", "business"]


async def classify_only(ctx: TurnContext, question: str) -> Literal["social", "unsupported", "business"]:
    """Gọi bộ phân loại rẻ cho một lượt. Không bao giờ ném lỗi ra ngoài — mọi
    lỗi hay quá giờ đều trả `"business"` (code ép đi tiếp vào Orchestrator,
    không bịa một nhãn xã giao/không hỗ trợ khi không chắc). Log một sự kiện
    tên ổn định trước khi trả về như vậy, không log chữ khách (§11)."""
    message = HumanMessage(content=f"{render_context(ctx, 'lite')}\n\n{UNTRUSTED}{question}")
    messages = [SystemMessage(content=CLASSIFY_PROMPT), message]

    model = build_chat_model(
        tags=[CLASSIFY_TAG], streaming=False, max_retries=1, reasoning_effort=None
    )
    structured = model.with_structured_output(ClassifyOut)

    try:
        result = await asyncio.wait_for(structured.ainvoke(messages), CLASSIFY_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("classify_failed", extra={"reason": type(exc).__name__})
        return "business"

    return result.route
