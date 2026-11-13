"""Dựng Orchestrator cho MỘT lượt (spec §3.2, §3.3; docs/architech-agent.md
luật 6, 8).

`create_agent` của Orchestrator CHÍNH LÀ graph của một lượt: tool giao việc +
`decide_pending` (`tools.py`), chồng middleware (`middleware/`), state và
context của gói (`state.py`), checkpointer dùng chung (thread =
`conversation_id`) và store bộ nhớ dài hạn.

`system_prompt=ORCHESTRATOR_PROMPT` là phần CỐ ĐỊNH (nằm trong prompt cache);
khối bối cảnh của lượt do `ContextMiddleware` chèn thành SystemMessage THỨ HAI
ở mỗi lần gọi model (CONTEXT.md bẫy 8-9).

Câu trả lời cuối là JSON các đoạn trích dẫn (spec level C §1): `reply_format()`
gắn `REPLY_SCHEMA` bằng `ProviderStrategy(strict=True)` tường minh — schema cố
định ở mọi lần gọi (prompt cache), không `ToolStrategy` (câu cuối sẽ thành tool
call kèm ToolMessage giả), không schema trần (factory tự chọn chiến lược).
"""
from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langgraph.graph.state import CompiledStateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.checkpoint import get_checkpointer
from app.agents.llm import build_chat_model
from app.agents.salon.guard.segments import REPLY_SCHEMA
from app.agents.salon.memory.store import get_store
from app.agents.salon.orchestrator.middleware import orchestrator_middleware
from app.agents.salon.orchestrator.prompt import ORCHESTRATOR_PROMPT
from app.agents.salon.orchestrator.state import SalonContext, SalonState
from app.agents.salon.orchestrator.tools import make_orchestrator_tools
from app.agents.salon.shared.context import SnapshotHolder
from app.core.config import settings
from app.models.user import User


def reply_format() -> ProviderStrategy:
    """Định dạng câu trả lời cuối: JSON strict theo `REPLY_SCHEMA`. Guard đọc
    `reply.text` (không đọc `structured_response`)."""
    return ProviderStrategy(schema=REPLY_SCHEMA, strict=True)


def build_orchestrator(db: AsyncIOMotorDatabase, user: User,
                       holder: SnapshotHolder) -> CompiledStateGraph:
    """Orchestrator của một lượt. Không stream (`streaming=False`): câu tới
    khách chỉ đi qua `complete` sau guard. `max_retries=0`: chỉ
    `ModelRetryMiddleware` thử lại, SDK không nhân thêm lần gọi."""
    store = get_store()
    return create_agent(
        model=build_chat_model(tags=["orchestrate"], streaming=False, max_retries=0,
                               reasoning_effort="low"),
        tools=make_orchestrator_tools(db, user, holder),
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=orchestrator_middleware(db, user, holder, store),
        state_schema=SalonState,
        context_schema=SalonContext,
        response_format=reply_format(),
        checkpointer=get_checkpointer(),
        store=store,
        name="orchestrator",
    )


def recursion_limit(agent: CompiledStateGraph) -> int:
    """`recursion_limit` cho config của một lượt — lưới an toàn, không phải trần.

    Mỗi hook middleware (`before_model`, `after_model`, …) là một node riêng, mỗi
    node chạy là một bước; một vòng gọi model đi qua mỗi node nhiều nhất một
    lần. Nên `số node × (trần gọi model + 1)` không bao giờ chạm trước
    `ModelCallLimitMiddleware` (`+1`: lần thử thứ trần+1 vẫn chạy các
    `before_model` rồi mới bị chặn). Không đặt thì `astream_events` lấy mặc
    định 25 của langchain-core — live run 1 (11/11) nổ ở lượt 2 agent con +
    1 lần viết lại."""
    nodes = sum(1 for name in agent.nodes if not name.startswith("__"))
    return nodes * (settings.salon_model_calls_per_turn + 1)
