import asyncio
from typing import Any, Awaitable, Callable, List, Optional, Sequence

import httpx
import openai
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRetryMiddleware
from langchain_core.exceptions import ModelError
from langchain_core.tools import BaseTool

from app.agents.booking_graph.context import live_queue, queued_line, with_line
from app.agents.booking_graph.middleware.guard import GuardMiddleware
from app.agents.booking_graph.middleware.layout import (  # noqa: F401 (re-export)
    STATE_HEADER, MessageLayoutMiddleware)
from app.agents.booking_graph.middleware.tools import (  # noqa: F401 (re-export)
    FALLBACK_ANSWER, MAX_TOOL_ROUNDS, TOOL_ERROR_TEXT, NoParallelToolsMiddleware,
    ToolErrorMiddleware, ToolRoundLimitMiddleware)
from app.agents.booking_graph.middleware.phrase import PhraseMiddleware
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.models.user import User

# Chỉ token mang tag này mới được đẩy ra màn hình khách (events.py lọc theo nó).
RESPOND_TAG = "respond"

# Lỗi TẠM THỜI của model agent (Azure 5xx/429, timeout 20 giây, mất kết nối):
# thử lại ĐÚNG một lần sau ~0,5 giây; vẫn lỗi (hay lỗi 4xx) thì ném — `run_turn` đổi thành câu "Máy đang bận..." như A.
MODEL_RETRIES = 1
MODEL_RETRY_DELAY_SECONDS = 0.5


def is_transient(exc: BaseException) -> bool:
    """Lỗi TẠM THỜI — thử lại có thể ra kết quả khác: timeout, mất kết nối,
    429, 5xx. 400 (kể cả content filter, quá context), 401/403/404 thì thử lại
    vẫn y vậy, chỉ tốn gấp đôi thời gian của khách (review M4 M-1).
    Lỗi lạ (lỗi lập trình) cũng không thử lại."""
    if isinstance(exc, ModelError):
        return exc.is_retryable
    if isinstance(exc, (openai.APIConnectionError, httpx.TimeoutException,
                        httpx.TransportError, asyncio.TimeoutError)):
        return True          # APITimeoutError là con của APIConnectionError
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def model_retry_middleware() -> ModelRetryMiddleware:
    return ModelRetryMiddleware(max_retries=MODEL_RETRIES, on_failure="error",
                                initial_delay=MODEL_RETRY_DELAY_SECONDS,
                                retry_on=is_transient)


class _LazyAgent:
    """Agent dựng ở lần gọi ĐẦU TIÊN, không phải lúc `build_graph`.

    `build_graph` chạy mỗi lượt và dựng 3 agent, nhưng mỗi lượt chỉ chạy một.
    Dựng model ngay thì (a) mất ~25 ms/model đồng bộ trên event loop, (b) thiếu
    key LLM là `build_graph` nổ — mất luôn nhánh confirm→phrase vốn chạy được
    như A, và test dựng graph phải có key thật. A dựng model lúc node chạy;
    giữ đúng như vậy.

    Cache trong `self` là cấu hình (model + graph đã compile), không phải trạng
    thái lượt chat — graph ngoài dựng lại mỗi lượt.
    """

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._agent = None

    def _get(self):
        if self._agent is None:
            self._agent = self._factory()
        return self._agent

    async def ainvoke(self, *args, **kwargs):
        return await self._get().ainvoke(*args, **kwargs)

    # KHÔNG có `__getattr__` uỷ quyền: LangGraph `compile()` dò subgraph bằng
    # `hasattr` trên closure của node — uỷ quyền là dựng model ngay lúc
    # build_graph, mất hết tác dụng của việc dựng lười.


def build_agent(
    prompt: str,
    tools: List[BaseTool],
    *,
    tag: str = RESPOND_TAG,
    model=None,
    user: Optional[User] = None,
    extra_middleware: Sequence[AgentMiddleware] = (),
):
    """Một agent `create_agent`: gọi tool rồi trả lời.

    `tag` gắn vào model để bộ phát sự kiện lọc được token: chỉ agent sinh câu
    trả lời cuối mới mang tag "respond", nên token định tuyến không lọt ra màn
    hình.

    Thứ tự middleware (ngoài → trong): có `user` thì Phrase rồi Guard đứng
    ngoài cùng; rồi `extra_middleware` (M2 thêm lịch sử, M3 thêm confirm), bố
    cục message, tắt parallel, giới hạn vòng, lỗi tool, thử lại model một
    lần. `after_agent` chạy
    NGƯỢC danh sách nên Guard chạy SAU CÙNG — kể cả khi Phrase hay giới hạn
    vòng nhảy thẳng tới cuối. Không có `user` (test agent trần) thì không có
    guard/phrase: agent chỉ trả câu thô.

    `checkpointer=False`: agent con không bao giờ ghi checkpoint riêng — state
    lượt chat thuộc graph ngoài.

    Không truyền `model` thì model thật chỉ được dựng ở lần gọi đầu (`_LazyAgent`).
    """
    outer = [PhraseMiddleware(), GuardMiddleware(user)] if user is not None else []
    middleware = [
        *outer,
        *extra_middleware,
        MessageLayoutMiddleware(),
        NoParallelToolsMiddleware(),
        ToolRoundLimitMiddleware(),
        ToolErrorMiddleware(),
        # Trong cùng (sát model): một lần thử lại chạy lại đúng lần gọi model,
        # các middleware ngoài không chạy lại.
        model_retry_middleware(),
    ]

    def factory(chat_model=None):
        return create_agent(
            # max_retries=0: SDK không tự thử lại — ModelRetryMiddleware lo (F11).
            chat_model or build_chat_model(tags=[tag], temperature=0.2, max_retries=0),
            tools,
            system_prompt=prompt,
            middleware=middleware,
            checkpointer=False,
        )

    if model is not None:
        return factory(model)
    return _LazyAgent(factory)


def make_agent_node(agent) -> Callable[[GraphState], Awaitable[dict]]:
    """Node LangGraph bọc một agent `build_agent`.

    `messages` là cả thread trong ngày; `HistoryWindowMiddleware` (trong
    `extra_middleware` của graph) cắt lại đúng cửa sổ A gửi model.

    Trả `draft` (câu thô của agent) và — khi agent có GuardMiddleware —
    `answer` / `answer_source` / `violations` do guard quyết. Tool message của
    lượt này ở lại trong agent con, KHÔNG quay về `messages` ngoài.

    `proposal_id` của lượt này (None nếu không có) — TRỪ lượt câu chốt
    (`confirm_fact`): agent không chạy tool nên không đụng tới đề xuất; câu
    hỏi lại (clarify) phải giữ nguyên đề xuất để confirm chờ tiếp.
    """
    async def node(state: GraphState) -> dict:
        # Hàng chờ (đặt lần lượt) → MỘT dòng code viết trong khối bối cảnh.
        queue = live_queue(state.get("queued"))
        out = await agent.ainvoke({
            "messages": state["messages"],
            "context_block": with_line(state.get("context_block", ""), queued_line(queue)),
            "queued": queue,
            "summary": state.get("summary") or [],
            "slots": state.get("slots"),
            # HistoryWindowMiddleware đọc mốc nén từ đây — thiếu là tin đã nén
            # bị gửi lại nguyên văn mỗi lượt (F2).
            "history_after": state.get("history_after"),
            "asked_at": state.get("asked_at"),
            # GuardMiddleware / PhraseMiddleware.
            "previous_replies": state.get("previous_replies") or [],
            "customer_text": state.get("customer_text") or "",
            "confirm_fact": state.get("confirm_fact"),
            "fallback": state.get("fallback"),
        })
        messages = out.get("messages") or []
        reply = messages[-1] if messages else None
        result = {"draft": getattr(reply, "content", "") or FALLBACK_ANSWER}
        for key in ("answer", "answer_source", "violations"):
            if key in out:
                result[key] = out[key]
        # Hàng chờ sau lượt này (ConfirmMiddleware có thể đã đổi). Không có gì
        # trước và sau thì không ghi khoá — đường cũ giữ nguyên.
        after = out.get("queued", queue)
        if after or state.get("queued"):
            result["queued"] = after
        if not state.get("confirm_fact"):
            # Đề xuất của lượt này (ConfirmMiddleware). Vòng tool cạn thì câu
            # cuối là FALLBACK chứ không phải câu hỏi xác nhận — không được chờ
            # duyệt (G-07).
            result["proposal_id"] = (None if out.get("tool_loop_exhausted")
                                     else out.get("proposal_id"))
        return result

    return node
