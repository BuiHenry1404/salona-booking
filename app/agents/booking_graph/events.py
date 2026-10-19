import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.agents import RESPOND_TAG
from app.agents.booking_graph.graph import build_graph
from app.agents.booking_graph.history import stamp, stamped_at
from app.core.clock import now_ms, to_local
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.models.user import User
from app.services.conversation import ConversationService, session_cutoff
from app.services.conversation_state import ConversationStateService

logger = get_logger(__name__)

# Trần thời gian của MỘT lượt (A-04): Azure treo thì khách nhận câu lỗi như A
# thay vì chờ mãi. Phải lớn hơn TỔNG ngân sách xấu nhất trên một đường của
# lượt, không chỉ phần agent: bước phân loại (supervisor 8 giây ở tin mới, bộ
# phân loại 8 giây ở resume) + một lần gọi model agent kèm thử lại (20 giây ×
# (1 + 1) + 0,5 giây) + rewrite của guard (8 giây) ≈ 56,5 giây. Có test chặn
# (`test_worst_case_llm_budget_of_one_turn_fits_under_the_turn_cap`): một lần
# gọi treo phải xuống cấp (fallback, thử lại), không ăn hết trần.
TURN_TIMEOUT_SECONDS = 60
TURN_ERROR_MESSAGE = "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."


# Giữ tham chiếu mạnh: event loop chỉ giữ weak-ref, tác vụ nền không ai giữ
# có thể bị GC giữa chừng — mất lượt nén mà không có dòng log nào.
_PENDING_COMPACTIONS: set = set()


def schedule_compaction(db: AsyncIOMotorDatabase, user_id: str) -> None:
    """Nén nền, không await: khách đã nhận complete. maybe_compact tự nuốt lỗi."""
    task = asyncio.create_task(ConversationStateService(db).maybe_compact(user_id))
    _PENDING_COMPACTIONS.add(task)
    task.add_done_callback(_PENDING_COMPACTIONS.discard)


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


def thread_id_for(user_id: str, now: datetime) -> str:
    """Một thread mỗi khách mỗi NGÀY VN — cùng ranh giới phiên với A.

    Theo giờ VN, không theo UTC: nửa đêm UTC là 7 giờ sáng ở VN, cắt giữa buổi.
    """
    return f"{user_id}:{to_local(now).date().isoformat()}"


async def _last_checkpoint_at(graph, thread_id: str) -> Optional[datetime]:
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    created_at = getattr(snap, "created_at", None)
    return datetime.fromisoformat(created_at) if created_at else None


async def pick_thread(graph, user_id: str, now: datetime) -> str:
    """Thread của lượt này: hôm nay, trừ ca nối qua nửa đêm.

    Luật 30 phút của A (khách được hỏi 23:58, đáp "ừ" 00:01) cần tin hôm qua.
    Thread hôm nay CHƯA có checkpoint mà thread hôm qua có checkpoint mới hơn
    mốc cắt phiên (`session_cutoff`: đầu ngày, hoặc 30 phút trước nếu sớm hơn)
    thì dùng tiếp thread hôm qua. So với mốc cắt phiên chứ không chỉ "30 phút
    gần nhất": khách nối lúc 00:10 rồi nhắn lúc 00:40 thì A vẫn giữ tin 00:10,
    nên thread cũng phải giữ. Cửa sổ model vẫn do `model_history` cắt.
    """
    today = thread_id_for(user_id, now)
    if await _last_checkpoint_at(graph, today) is not None:
        return today
    yesterday = thread_id_for(user_id, to_local(now) - timedelta(days=1))
    last = await _last_checkpoint_at(graph, yesterday)
    if last is not None and last >= session_cutoff(now):
        return yesterday
    return today


def fresh_turn_fields(context: dict, question: str) -> dict:
    """Mọi khoá một lượt sinh ra, đặt lại cho lượt mới.

    Thread checkpointer GIỮ state lượt trước: khoá nào không có trong input là
    mang giá trị cũ sang — `answer` cũ thành câu đáp của lượt không sinh câu nào,
    `confirm_fact` cũ làm agent booking tưởng đang chốt lịch (PhraseMiddleware
    thay model) và guard kiểm theo luật câu chốt.

    KHÔNG có `proposal_id` ở đây: lượt resume phải giữ nguyên đề xuất đang chờ;
    lượt mới (không resume) tự đặt `proposal_id=None` trong `run_turn`.
    """
    return {
        "answer": "",
        "answer_source": "llm",
        "draft": "",
        "violations": [],
        "route": "",
        "gate_next": "",
        "fallback": None,
        "confirm_fact": None,
        "context_block": context["context_block"],
        "summary": context.get("summary") or [],
        "slots": context.get("slots"),
        "history_after": context.get("history_after"),
        "previous_replies": [m.content for m in context.get("history") or []
                             if m.role == "assistant" and m.source == "llm"],
        "customer_text": question,
        "user_id": context.get("user_id", ""),
    }


# Lượt đang chạy của từng khách. Một tiến trình, một worker (Telegram long
# polling bắt buộc) nên khoá trong bộ nhớ là đủ.
_ACTIVE_TURNS: Dict[str, asyncio.Task] = {}
SUPERSEDE_WAIT_SECONDS = 2.0


async def _supersede(user_id: str, me: asyncio.Task,
                     previous: Optional[asyncio.Task]) -> None:
    """Huỷ lượt cũ còn chạy của cùng khách và chờ nó dừng (tối đa 2 giây — quá
    thì chạy tiếp, không để khách chờ theo một lượt đã treo).

    Lượt này đã được ĐĂNG KÝ trước khi gọi hàm này: lượt thứ ba tới trong lúc
    ta còn chờ sẽ huỷ ta chứ không huỷ lại lượt đầu, nên không bao giờ có hai
    lượt cùng chạy trên một thread.
    """
    if previous is not None and previous is not me and not previous.done():
        previous.cancel()
        await asyncio.wait({previous}, timeout=SUPERSEDE_WAIT_SECONDS)


async def run_turn(
    db: AsyncIOMotorDatabase, user: User, question: str
) -> AsyncIterator[AgentEvent]:
    """Chạy một lượt chat và phát sự kiện theo thứ tự thời gian thực.

    Lịch sử model nằm trong thread checkpointer (`pick_thread`); `conversations`
    vẫn được ghi như cũ làm nhật ký hiển thị, cùng mốc thời gian với thread.

    Mỗi khách chỉ một lượt chạy: lượt mới huỷ lượt cũ còn treo (`_supersede`).
    """
    user_id = str(user.id)
    me = asyncio.current_task()
    previous = _ACTIVE_TURNS.get(user_id)
    if me is not None:
        _ACTIVE_TURNS[user_id] = me          # đăng ký TRƯỚC khi huỷ/chờ lượt cũ
    try:
        await _supersede(user_id, me, previous)
        async for event in _run_turn(db, user, question):
            yield event
    except asyncio.CancelledError:
        # Bị lượt mới thay: im lặng — không `error`, không ghi `conversations`
        # (lượt mới đang trả lời khách). Huỷ vì lý do khác thì không log nhầm.
        if _ACTIVE_TURNS.get(user_id) is not me:
            logger.info("turn_superseded", extra={"user_id": user_id})
        raise
    finally:
        if me is not None and _ACTIVE_TURNS.get(user_id) is me:
            del _ACTIVE_TURNS[user_id]


async def _run_turn(
    db: AsyncIOMotorDatabase, user: User, question: str
) -> AsyncIterator[AgentEvent]:
    from app.agents.booking_graph.context import load_context

    user_id = str(user.id)
    conversations = ConversationService(db)

    yield AgentEvent("turn_started")

    # Trần thời gian chỉ bọc lúc CHỜ graph (và các lần đọc/ghi state của nó),
    # không bao giờ bọc `yield`: hết giờ khi người nhận đang `sio.emit` thì
    # CancelledError nổ ở phía người nhận, không có `error` nào được gửi (F12).
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TURN_TIMEOUT_SECONDS

    async def bounded(awaitable):
        async with asyncio.timeout_at(deadline):
            return await awaitable

    try:
        # Một mốc cho câu khách ở CẢ HAI nơi (thread và conversations):
        # covers_until đọc từ conversations phải khớp đúng tin bên thread.
        asked_at = now_ms()
        context = await bounded(load_context(db, user, question))
        graph = build_graph(db, user)
        thread_id = await bounded(pick_thread(graph, user_id, asked_at))

        # Langfuse v3: handler không mang danh tính, danh tính đi qua metadata.
        # session_id = user_id (không phải thread_id): trace của một khách
        # vẫn gom về một chỗ dù thread đổi theo ngày.
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": get_callbacks(),
            "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id),
        }
        fields = {
            "messages": [stamp(HumanMessage(content=question), asked_at)],
            **fresh_turn_fields(context, question),
            # Mốc "bây giờ" của cả lượt cho cửa sổ lịch sử — như A, tính một lần
            # lúc nạp bối cảnh, không tính lại ở từng lần gọi model.
            "asked_at": asked_at,
        }
        snapshot = await bounded(graph.aget_state(config))
        # Đang chờ = có interrupt, HOẶC confirm là bước kế tiếp mà chưa kịp
        # interrupt VÀ có đề xuất: lượt resume trước bị huỷ SAU `aupdate_state`
        # (checkpoint mới, next=confirm) nhưng trước khi confirm xong — coi đó là
        # tin mới thì "ừ" thứ hai đi vào supervisor, khách không nghe câu chốt.
        # Không có đề xuất thì next=confirm chỉ là lượt bị cắt sau record: tin
        # sau là tin MỚI, resume ở đây là trả `complete` rỗng (review M3).
        values = getattr(snapshot, "values", None) or {}
        waiting = bool(getattr(snapshot, "interrupts", ())) or (
            "confirm" in (getattr(snapshot, "next", None) or ())
            and bool(values.get("proposal_id")))
        if waiting:
            # Thread đang chờ khách trả lời đề xuất (node confirm interrupt ở
            # lượt trước): câu khách là câu trả lời. Các khoá lượt mới ghi
            # TRƯỚC bằng `aupdate_state` (như thể `record` vừa ghi — cạnh
            # record → confirm có sẵn), rồi resume KHÔNG kèm `update`.
            #
            # Không dùng `Command(resume, update=fields)`: lượt resume bị huỷ
            # giữa chừng (lượt mới thay) để lại `update` của nó thành pending
            # write trên checkpoint đang chờ; lần resume sau ghi `answer` LẦN
            # HAI trong cùng một bước → InvalidUpdateError, khách nhận "Máy
            # đang bận" mãi (review M3). `aupdate_state` tạo checkpoint mới nên
            # pending write cũ bị bỏ, và node vẫn nhận câu khách MỚI NHẤT.
            #
            # KHÔNG đặt lại `proposal_id` — đó chính là thứ đang chờ duyệt.
            await bounded(graph.aupdate_state(config, fields, as_node="record"))
            turn_input = Command(resume=question)
        else:
            # Tin mới: chạy từ START. Đề xuất cũ (nếu còn trong checkpoint,
            # vd. lượt trước lỗi giữa chừng) không bao giờ được mang sang.
            turn_input = {**fields, "proposal_id": None}

        stream = graph.astream_events(turn_input, config=config, version="v2")
        while True:
            try:
                raw = await bounded(anext(stream))
            except StopAsyncIteration:
                break
            for event in translate_langchain_event(raw):
                yield event

        values = (await bounded(graph.aget_state(config))).values or {}
        answer = values.get("answer", "")
        if answer:
            last = (values.get("messages") or [None])[-1]
            answered_at = stamped_at(last) if isinstance(last, AIMessage) else None

            async def persist():
                await conversations.append(user_id, "user", question, created_at=asked_at)
                await conversations.append(user_id, "assistant", answer,
                                           source=values.get("answer_source", "llm"),
                                           created_at=answered_at or now_ms())

            # `record` đã ghi câu đáp vào thread: nhật ký phải ghi CẢ CẶP. Bị
            # huỷ (lượt mới thay) giữa hai lần ghi thì `conversations` có câu
            # khách lẻ — shield để cặp ghi xong dù lượt này đã bị huỷ.
            await asyncio.shield(asyncio.ensure_future(persist()))

        yield AgentEvent("complete", {"answer": answer})

        if answer:
            # Sau complete, ngoài đường trả lời. Lỗi lên lịch (hiếm) cũng không
            # được biến thành sự kiện error cho một lượt đã xong.
            try:
                schedule_compaction(db, user_id)
            except Exception as exc:
                logger.warning("state_schedule_failed", extra={
                    "user_id": user_id, "error": str(exc), "error_type": type(exc).__name__,
                })

    except Exception as exc:
        if isinstance(exc, TimeoutError) and loop.time() >= deadline:
            logger.error("agent_turn_timeout", extra={"user_id": user_id,
                                                      "seconds": TURN_TIMEOUT_SECONDS})
        else:
            logger.error("agent_turn_failed", extra={"user_id": user_id, "error": str(exc)})
        yield AgentEvent("error", {"message": TURN_ERROR_MESSAGE})
