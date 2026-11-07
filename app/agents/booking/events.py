"""`run_turn` trên graph mới (Task 5.4, §5.3/§6/§9.1/§10/§11 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

Khác bản cũ (`events.py` của gói cũ): KHÔNG còn `interrupt()` /
`Command(resume=...)` / `aupdate_state` — mỗi lượt (kể cả nút Đồng ý/Không)
chạy graph mới (`app.agents.booking.graph.build_graph`) từ `START`. "Đang
chờ" không còn là trạng thái của checkpoint; nó là `TurnContext.pending`, đọc
lại từ `proposals` ngay trong node `resolve`. Nút bấm khi không còn gì để
duyệt vẫn chạy hết graph (route "button" → `button_path` tự trả
`stale_button`), không rẽ nhánh riêng ở tầng này như bản cũ.

Các hàm hạ tầng (`translate_langchain_event`, `thread_id_for`, `pick_thread`,
`_supersede`/`_ACTIVE_TURNS`, `schedule_compaction`) SAO CHÉP nguyên văn từ
`events.py` của gói cũ — M6 sẽ xoá bản cũ, không import ngược từ đó.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import TurnContext
from app.agents.booking.conversation import RESPOND_TAG
from app.agents.booking.graph import build_graph
from app.agents.booking.ledger import TurnLedger
from app.agents.booking.orchestrator.agent import OrchestrateOutcome
from app.agents.booking.progress import turn_facts
from app.agents.booking.reply_brief import ButtonOutcome
from app.agents.booking.resolver import ResolveResult
from app.agents.booking.button import ButtonReply
from app.agents.booking.history import stamp, stamped_at
from app.core.clock import now_ms, now_utc, to_local
from app.core.config import settings
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.core.text import single_line
from app.core.vi_format import format_vi_datetime
from app.models.user import User
from app.services.conversation import ConversationService, session_cutoff
from app.services.conversation_state import ConversationStateService
from app.services.proposal import ProposalService

logger = get_logger(__name__)

TURN_ERROR_MESSAGE = "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."

# §9.1: input ≤ 1000 ký tự, ép một dòng.
CUSTOMER_TEXT_MAX_CHARS = 1000


def turn_timeout_seconds() -> int:
    """Trần thời gian của MỘT lượt (`settings.orch_turn_timeout_seconds`) —
    đọc LÚC GỌI, không đóng băng thành hằng số ở import time, để test
    monkeypatch được `settings`."""
    return settings.orch_turn_timeout_seconds


# --- sao chép nguyên văn từ `events.py` của gói cũ (M6 xoá bản cũ) ----------

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
    token JSON định tuyến/tool của Orchestrator sẽ chạy ngang màn hình khách.
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

    Luật 30 phút (khách được hỏi 23:58, đáp "ừ" 00:01) cần tin hôm qua.
    Thread hôm nay CHƯA có checkpoint mà thread hôm qua có checkpoint mới hơn
    mốc cắt phiên (`session_cutoff`: đầu ngày, hoặc 30 phút trước nếu sớm hơn)
    thì dùng tiếp thread hôm qua. So với mốc cắt phiên chứ không chỉ "30 phút
    gần nhất": khách nối lúc 00:10 rồi nhắn lúc 00:40 vẫn giữ tin 00:10, nên
    thread cũng phải giữ. Cửa sổ model vẫn do `model_history` cắt.
    """
    today = thread_id_for(user_id, now)
    if await _last_checkpoint_at(graph, today) is not None:
        return today
    yesterday = thread_id_for(user_id, to_local(now) - timedelta(days=1))
    last = await _last_checkpoint_at(graph, yesterday)
    if last is not None and last >= session_cutoff(now):
        return yesterday
    return today


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


# --- mới ở Task 5.4 ----------------------------------------------------------


async def pending_proposal_payload(
    db: AsyncIOMotorDatabase, user: User, *, ids: Optional[dict] = None,
) -> Optional[dict]:
    """Khối "đề xuất đang chờ" đính kèm `complete` cho nút Đồng ý/Không (§6).

    Khác `None` CHỈ khi `ProposalService.latest(user)` còn `pending` và CHƯA
    hết hạn — đọc lại từ DB (không tin state đồ hoạ) vì lượt này (hay một lượt
    bị `_supersede` chen giữa) có thể vừa approve/reject/tạo đề xuất mới. Lỗi
    đọc chỉ đáng mất NÚT bấm (`proposal: None`), không đáng mất cả lượt — câu
    đáp đã `persist` xong trước khi hàm này chạy. `ids` (nếu `_run_turn`
    truyền vào) gắn `turn_id`/`thread_id` vào dòng log lỗi — fallback về
    `user_id` một mình khi gọi độc lập (test/nơi khác không có `ids`, §11).
    """
    try:
        proposal = await ProposalService(db).latest(user)
    except Exception as exc:
        logger.warning("pending_proposal_payload_failed", extra={
            **(ids or {"user_id": str(user.id)}), "error_type": type(exc).__name__,
        })
        return None
    if proposal is None or proposal.status != "pending" or proposal.expires_at <= now_utc():
        return None
    return {
        "id": str(proposal.id),
        "kind": proposal.kind,
        "expires_at": to_local(proposal.expires_at).isoformat(),
        "when_spoken": format_vi_datetime(proposal.start_at),
    }


async def _turn_window(db: AsyncIOMotorDatabase, user_id: str) -> tuple:
    """`history_after` (= `covers_until` của state nén) và `previous_replies`
    (câu đáp LLM gần nhất — `guard.repeats` đọc để không lặp câu). Đọc TRƯỚC
    khi `resolve` của lượt này chạy, như A đọc bối cảnh một lần lúc vào lượt."""
    conversations = ConversationService(db)
    state = await conversations.get_state(user_id)
    after = state.covers_until if state else None
    history = await conversations.history(user_id, after=after)
    previous_replies = [m.content for m in history if m.role == "assistant" and m.source == "llm"]
    return after, previous_replies


def _restore_outcome(raw: Optional[dict]):
    """`state["outcome"]` (dict JSON) → `OrchestrateOutcome`/`ButtonOutcome` —
    cùng luật với `graph._outcome`, viết lại ở đây để `events.py` không phụ
    thuộc một tên nội bộ (`_`) của module khác."""
    if not raw:
        return None
    data = {k: v for k, v in raw.items() if k != "type"}
    if raw.get("type") == "button":
        return ButtonOutcome.model_validate(data)
    return OrchestrateOutcome.model_validate(data)


async def _apply_turn_facts(db: AsyncIOMotorDatabase, user: User, values: dict, ids: dict) -> None:
    """Ghi `TurnFacts` (§8.3) vào slots NGAY sau `complete` — fail-soft: lỗi ở
    đây chỉ đáng mất dữ kiện slots của một lượt, không được biến `complete`
    đã phát thành `error`."""
    try:
        ctx = TurnContext.model_validate(values["turn_context"])
        resolve = ResolveResult.model_validate(values["resolve"])
        outcome = _restore_outcome(values.get("outcome"))
        facts = turn_facts(outcome, resolve, ctx)
        await ConversationStateService(db).apply_turn_analysis(
            str(user.id), facts, last_bot_text=ctx.last_bot_text,
        )
    except Exception as exc:
        logger.warning("turn_facts_apply_failed", extra={**ids, "error_type": type(exc).__name__})


async def _after_complete(
    db: AsyncIOMotorDatabase, user: User, values: dict, turn_id: str, ids: dict,
) -> None:
    """Mọi lệnh ghi SAU khi `complete` đã phát — `mark_announced` (lượt này +
    `announced_turn_id` của R0) và `TurnFacts` — chạy trong ĐÚNG MỘT task nền,
    gọi qua `asyncio.shield` (fix round 1, §10).

    Lý do shield: khách có thể gõ tin kế tiếp NGAY sau khi nhận `complete`
    (trước khi `_run_turn` kịp chạy xong phần sau) — `_supersede` của lượt mới
    sẽ `cancel()` task đang chạy hàm này. Không shield thì `mark_announced`
    không bao giờ kịp ghi, và R0 lặp lại y nguyên dòng báo tin ở lượt sau
    (khách nghe "em đã đặt xong..." hai lần). Mỗi bước tự log lỗi riêng —
    `mark_announced(announced_turn_id)` thất bại không được chặn `TurnFacts`.
    """
    answer = values.get("answer") or ""
    if answer:
        # Đánh dấu ĐÃ BÁO chỉ khi thật sự có câu đáp gửi khách (fix round 1,
        # #5) — câu đáp rỗng nghĩa là không có gì được nói, không đáng gọi là
        # "đã báo" cho lượt này lẫn cho `announced_turn_id` của R0.
        ledger = TurnLedger(db)
        try:
            await ledger.mark_announced(turn_id)
        except Exception as exc:
            logger.warning("mark_announced_failed", extra={**ids, "error_type": type(exc).__name__})
        announced_turn_id = values.get("announced_turn_id")
        if announced_turn_id:
            try:
                await ledger.mark_announced(announced_turn_id)
            except Exception as exc:
                logger.warning("mark_announced_failed", extra={
                    **ids, "error_type": type(exc).__name__, "announced_turn_id": announced_turn_id,
                })

    # Sau complete, cạnh `persist`: dữ kiện lượt vào slots NGAY (lượt sau thấy
    # `stated` mà không chờ nén nền).
    await _apply_turn_facts(db, user, values, ids)


async def run_turn(
    db: AsyncIOMotorDatabase, user: User, question: "str | ButtonReply"
) -> AsyncIterator[AgentEvent]:
    """Chạy một lượt chat trên graph mới và phát sự kiện theo thứ tự thời gian thực.

    `question` là câu khách gõ, HOẶC `ButtonReply` (khách bấm Đồng ý/Không):
    `label` của nó đóng vai câu khách (`customer_text`, ghi vào `conversations`
    và thread); `decision`/`proposal_id` đi vào `state["button"]`, node
    `button_path` của graph tự đọc (không `Command(resume=...)` nào cả).

    `token` chỉ là nháp: model thử lại giữa stream thì token stream lại từ đầu.
    Mỗi lượt kết thúc bằng ĐÚNG một `complete` (câu cuối LUÔN là `state["answer"]`
    đã qua guard, KHÔNG BAO GIỜ là chữ ghép từ các token đã stream — client thay
    bubble bằng nó) hoặc `error` — xem AGENTS.md "Socket.IO protocol".

    Mỗi khách chỉ một lượt chạy: lượt mới huỷ lượt cũ còn treo (`_supersede`).
    """
    user_id = str(user.id)
    me = asyncio.current_task()
    previous = _ACTIVE_TURNS.get(user_id)
    if me is not None:
        _ACTIVE_TURNS[user_id] = me          # đăng ký TRƯỚC khi huỷ/chờ lượt cũ
    # Holder MUTABLE: `_run_turn` điền `turn_id`/`thread_id` ngay khi tính
    # được, để log `turn_superseded` ở ĐÂY (nơi duy nhất quyết log, tránh đếm
    # đôi — `test_three_quick_turns_only_the_newest_runs`) vẫn mang đủ ba khoá
    # §11 thay vì chỉ `user_id` (fix round 1, #3). Huỷ có thể xảy ra TRƯỚC khi
    # `_run_turn` kịp gán (vd. đang `await _supersede` của CHÍNH lượt mới) —
    # khi đó log chỉ có `user_id`, chấp nhận được (không còn gì khác để log).
    ids = {"user_id": user_id, "turn_id": None, "thread_id": None}
    try:
        await _supersede(user_id, me, previous)
        async for event in _run_turn(db, user, question, ids):
            yield event
    except asyncio.CancelledError:
        # Bị lượt mới thay: im lặng với KHÁCH — không `error`, không ghi
        # `conversations` (lượt mới đang trả lời khách). Huỷ vì lý do khác thì
        # không log nhầm `turn_superseded`.
        if _ACTIVE_TURNS.get(user_id) is not me:
            logger.info("turn_superseded", extra=ids)
        raise
    finally:
        if me is not None and _ACTIVE_TURNS.get(user_id) is me:
            del _ACTIVE_TURNS[user_id]


async def _run_turn(
    db: AsyncIOMotorDatabase, user: User, question: "str | ButtonReply", ids: dict,
) -> AsyncIterator[AgentEvent]:
    user_id = str(user.id)
    conversations = ConversationService(db)

    yield AgentEvent("turn_started")

    # Trần thời gian chỉ bọc lúc CHỜ graph (và các lần đọc/ghi state của nó),
    # không bao giờ bọc `yield`: hết giờ khi người nhận đang `sio.emit` thì
    # CancelledError nổ ở phía người nhận, không có `error` nào được gửi.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + turn_timeout_seconds()

    async def bounded(awaitable):
        async with asyncio.timeout_at(deadline):
            return await awaitable

    turn_id: Optional[str] = None
    thread_id: Optional[str] = None
    try:
        # Chuẩn hoá đầu vào (§9.1) TRONG try: một `question` dị dạng (không
        # phải `str`/`ButtonReply`) phải vẫn phát đúng MỘT `error`, không làm
        # generator vỡ ngang (fix round 1, #4).
        button = question if isinstance(question, ButtonReply) else None
        # Nút bấm KHÔNG gõ chữ — "label" ("Đồng ý"/"Không") đóng vai câu khách
        # ở mọi nơi dùng câu khách làm văn bản (thread, `conversations`).
        raw_text = button.label if button is not None else question
        question_text = single_line(raw_text, CUSTOMER_TEXT_MAX_CHARS) or ""

        # Một mốc cho câu khách ở CẢ HAI nơi (thread và conversations):
        # covers_until đọc từ conversations phải khớp đúng tin bên thread.
        asked_at = now_ms()
        turn_id = uuid.uuid4().hex
        graph = build_graph(db, user)
        thread_id = await bounded(pick_thread(graph, user_id, asked_at))
        history_after, previous_replies = await bounded(_turn_window(db, user_id))
        ids["turn_id"], ids["thread_id"] = turn_id, thread_id

        # Langfuse v3: handler không mang danh tính, danh tính đi qua metadata.
        # session_id = user_id (không phải thread_id): trace của một khách
        # vẫn gom về một chỗ dù thread đổi theo ngày.
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": get_callbacks(),
            "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id),
        }
        # LUÔN chạy từ START — không `aupdate_state`, không `Command(resume=...)`:
        # mọi khoá của lượt này phải có mặt, kể cả khi trống/None, vì thread
        # checkpointer giữ nguyên giá trị lượt trước cho khoá nào không có ở đây.
        fields = {
            "messages": [stamp(HumanMessage(content=question_text), asked_at)],
            "turn_id": turn_id,
            "asked_at": asked_at,
            "customer_text": question_text,
            "button": asdict(button) if button is not None else None,
            "history_after": history_after,
            "previous_replies": previous_replies,
        }

        stream = graph.astream_events(fields, config=config, version="v2")
        while True:
            try:
                raw = await bounded(anext(stream))
            except StopAsyncIteration:
                break
            for event in translate_langchain_event(raw):
                yield event

        snapshot = await bounded(graph.aget_state(config))
        values = snapshot.values or {}
        # Ghi chú controller (bắt buộc): câu cuối LUÔN là `state["answer"]` đã
        # qua guard — KHÔNG BAO GIỜ chữ ghép từ các token đã stream (model thử
        # lại giữa stream, hay guard viết lại nguyên câu, thì `complete` vẫn
        # phải mang đúng câu cuối).
        answer = values.get("answer", "")

        if answer:
            last = (values.get("messages") or [None])[-1]
            answered_at = stamped_at(last) if isinstance(last, AIMessage) else None

            async def persist():
                await conversations.append(user_id, "user", question_text, created_at=asked_at)
                await conversations.append(user_id, "assistant", answer,
                                           source=values.get("answer_source", "llm"),
                                           created_at=answered_at or now_ms())

            # `record` đã ghi câu đáp vào thread: nhật ký phải ghi CẢ CẶP. Bị
            # huỷ (lượt mới thay) giữa hai lần ghi thì `conversations` có câu
            # khách lẻ — shield để cặp ghi xong dù lượt này đã bị huỷ.
            try:
                await asyncio.shield(asyncio.ensure_future(persist()))
            except Exception as exc:
                # §10: "ghi lịch xong nhưng conversations/ledger ghi hỏng" —
                # lịch (nếu lượt này có ghi) là sự thật; mất một dòng sổ sách
                # không được biến một lượt đã có câu đáp thành `error`.
                logger.error("conversations_persist_failed", extra={
                    **ids, "error_type": type(exc).__name__,
                })
                try:
                    await TurnLedger(db).finish(
                        turn_id, ended_by="persist_failed", reply_kind=values.get("reply_kind"),
                    )
                except Exception as ledger_exc:
                    logger.warning("ledger_finish_failed", extra={
                        **ids, "error_type": type(ledger_exc).__name__,
                    })

        proposal = await pending_proposal_payload(db, user, ids=ids)
        yield AgentEvent("complete", {"answer": answer, "proposal": proposal})

        # Ghi chú controller (sau Task 5.3) + fix round 1 (#1): mọi lệnh ghi
        # SAU `complete` (đánh dấu ĐÃ BÁO cho lượt này VÀ cho `announced_turn_id`
        # của R0, `TurnFacts`) chạy trong ĐÚNG MỘT task `shield` — khách có thể
        # gõ tin kế tiếp ngay khi nhận `complete`, `_supersede` của lượt mới sẽ
        # `cancel()` task này nếu không shield, và `announced`/slots không bao
        # giờ kịp ghi.
        await asyncio.shield(asyncio.ensure_future(_after_complete(db, user, values, turn_id, ids)))

        if answer:
            # Sau complete, ngoài đường trả lời. Lỗi lên lịch (hiếm) cũng không
            # được biến thành sự kiện error cho một lượt đã xong.
            try:
                schedule_compaction(db, user_id)
            except Exception as exc:
                logger.warning("state_schedule_failed", extra={**ids, "error_type": type(exc).__name__})

    except Exception as exc:
        if isinstance(exc, TimeoutError) and loop.time() >= deadline:
            logger.error("agent_turn_timeout", extra={**ids, "seconds": turn_timeout_seconds()})
        else:
            logger.error("agent_turn_failed", extra={**ids, "error_type": type(exc).__name__}, exc_info=True)
        yield AgentEvent("error", {"message": TURN_ERROR_MESSAGE})
