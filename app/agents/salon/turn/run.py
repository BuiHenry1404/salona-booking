"""`run_turn` — một lượt chat trọn vẹn, gõ chữ hoặc nút bấm (spec §3.2, §3.4,
§5, §5.1, §8; docs/architech-agent.md luật 3, 7, 8).

Thứ tự một lượt:

1. Một lượt mỗi khách: lượt mới ĐĂNG KÝ mình trước, rồi huỷ lượt cũ còn chạy và
   chờ nó dừng tối đa `SUPERSEDE_WAIT_SECONDS` (gõ chữ và nút bấm dùng chung khoá).
2. Cuộc trò chuyện (`ConversationThreadService.current`, SAU bước huỷ để hai
   lượt không cùng mở cuộc mới); `thread_id = conversation_id` cũng là session
   Langfuse. Lượt đầu của cuộc mới gom bộ nhớ dài hạn từ cuộc trước.
3. `TurnLedger.start` trước mọi lệnh ledger khác; `SnapshotHolder` đọc qua chính
   ledger đó.
4. Nút bấm = API bằng code: kiểm nút, `apply_decision`, câu báo do code viết,
   không gọi model — trừ khi vừa duyệt mà phiên Đặt lịch còn yêu cầu kế
   (spec §5.1): khi đó Orchestrator chạy tiếp để đề xuất lịch kế.
   Gõ chữ: Orchestrator (`astream_events` v2), chỉ phát sự kiện tool có nhãn.
5. Lỗi trần / ngân sách / timeout / model: câu mẫu code dựng từ ledger + state
   đã lưu (hoặc `TURN_ERROR_MESSAGE` khi lượt chưa làm được gì), đóng phiên mở
   trong lượt, ghi thread bằng MỘT `aupdate_state` không để lại tool call dở.
6. Ghi ledger + `conversations` (trong `asyncio.shield`), phát `complete`, rồi
   đánh dấu đã báo (R0) — chỉ sau khi khách thật sự nhận câu.

Lượt bị lượt mới huỷ: đóng các phiên nó mở (khách chưa nghe câu hỏi), trong
`asyncio.shield`, rồi ném lại `CancelledError`. Log có cấu trúc luôn mang
`user_id` / `turn_id` / `thread_id`, không bao giờ mang chữ khách.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Literal, Optional

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langgraph.graph.state import CompiledStateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.salon.guard.facts import ReplyFacts, build_reply_facts
from app.agents.salon.guard.fallback import BUTTON_STALE_REPLY, button_reply, fallback_reply
from app.agents.salon.memory.long_term import consolidate_memory
from app.agents.salon.memory.store import get_store
from app.agents.salon.orchestrator.agent import build_orchestrator, recursion_limit
from app.agents.salon.orchestrator.decide import apply_decision
from app.agents.salon.orchestrator.middleware.budget import LlmBudgetExceeded
from app.agents.salon.orchestrator.middleware.turn_start import broken_message_ids
from app.agents.salon.orchestrator.middleware.turns import BUTTON_MARK, has_calls, previous_replies
from app.agents.salon.orchestrator.state import (
    SalonContext,
    read_facts,
    read_pending_outcome,
)
from app.agents.salon.shared.context import SnapshotHolder
from app.agents.salon.shared.schemas import (
    PendingOutcome,
    ProposalFact,
    TurnFact,
    UnannouncedDecision,
)
from app.agents.salon.turn.events import AgentEvent, tool_event
from app.agents.salon.turn.ledger import EndedBy, TurnLedger
from app.core.clock import now_ms, now_utc, to_local
from app.core.config import settings
from app.core.langfuse import get_callbacks, get_trace_metadata
from app.core.logging import get_logger
from app.core.text import detect_self_address, normalize_for_match, single_line
from app.core.vi_format import format_vi_datetime
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.conversation import ConversationService
from app.services.conversation_thread import ConversationThreadService
from app.services.proposal import ProposalService
from app.services.waiting_session import WaitingSessionService

logger = get_logger(__name__)

TURN_ERROR_MESSAGE = "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."

# Chữ khách: một dòng, tối đa 1000 ký tự (như gói cũ).
CUSTOMER_TEXT_MAX_CHARS = 1000

# Chữ trên nút — ghi vào `conversations` thay cho câu khách gõ.
BUTTON_LABELS = {"yes": "Đồng ý", "no": "Không"}

SUPERSEDE_WAIT_SECONDS = 2.0

CODE_SOURCE = {"salon_source": "code"}

_LIMIT_ERRORS = (ModelCallLimitExceededError, ToolCallLimitExceededError)

# Lượt đang chạy của từng khách. Một tiến trình, một worker (Telegram long
# polling bắt buộc) nên khoá trong bộ nhớ là đủ.
_ACTIVE_TURNS: dict[str, asyncio.Task] = {}

# Giữ tham chiếu mạnh tới việc ghi đang được shield — event loop chỉ giữ
# weak-ref, lượt bị huỷ thì không còn ai `await` chúng.
_INFLIGHT: set[asyncio.Future] = set()


@dataclass
class ButtonReply:
    """Khách bấm Đồng ý (`yes`) / Không (`no`) trên đề xuất `proposal_id`."""

    proposal_id: str
    decision: Literal["yes", "no"]


@dataclass
class _Answer:
    text: str
    source: Literal["llm", "code"]
    ended_by: EndedBy
    facts: list[TurnFact] = field(default_factory=list)
    # `turn_id` trong sổ lượt mà câu của MODEL đã báo (guard ghi
    # `announced_turn_ids` khi nhận câu); câu mẫu code để rỗng (spec level C §5).
    announced: list[str] = field(default_factory=list)


@dataclass
class _Box:
    """Kết quả của một nhánh lượt (nhánh là async generator phát sự kiện tool)."""

    answer: Optional[_Answer] = None


@dataclass
class _Progress:
    """Lượt đã tới đâu — để nhánh huỷ biết có phải đóng phiên không."""

    user_id: str
    turn_id: Optional[str] = None
    thread_id: Optional[str] = None
    completed: bool = False
    # Lượt đã đánh dấu ĐÃ BÁO trước `complete` (nhánh nút + yêu cầu kế) — bị
    # huỷ trước `complete` thì phải hoàn lại (Ruling R14).
    premarked: bool = False

    def ids(self) -> dict[str, Optional[str]]:
        return {"user_id": self.user_id, "turn_id": self.turn_id, "thread_id": self.thread_id}


@dataclass
class _Turn:
    db: AsyncIOMotorDatabase
    user: User
    turn_id: str
    thread_id: str
    asked_at: datetime
    kind: Literal["typed", "button"]
    ledger: TurnLedger
    holder: SnapshotHolder
    agent: CompiledStateGraph
    config: dict
    progress: _Progress

    @property
    def ids(self) -> dict[str, str]:
        return {"user_id": str(self.user.id), "turn_id": self.turn_id, "thread_id": self.thread_id}

    @property
    def opener_id(self) -> str:
        """Id của message mở lượt — để tìm lại phần thread của lượt này."""
        return f"turn-{self.turn_id}"

    def context(self, *, question: str, preset: Optional[PendingOutcome]) -> SalonContext:
        return SalonContext(user_id=str(self.user.id), turn_id=self.turn_id,
                            thread_id=self.thread_id, asked_at=self.asked_at,
                            question=question, preset=preset)


# ---------------------------------------------------------------------------
# Hạ tầng nhỏ
# ---------------------------------------------------------------------------


async def _shielded(awaitable: Awaitable[Any]) -> Any:
    """Chạy tới cùng dù lượt bị huỷ giữa chừng (ghi DB không bỏ dở)."""
    task = asyncio.ensure_future(awaitable)
    _INFLIGHT.add(task)
    task.add_done_callback(_INFLIGHT.discard)
    return await asyncio.shield(task)


async def _supersede(me: Optional[asyncio.Task], previous: Optional[asyncio.Task]) -> None:
    """Huỷ lượt cũ còn chạy của cùng khách và chờ nó dừng (tối đa 2 giây — quá
    thì chạy tiếp, không để khách chờ theo một lượt đã treo). Lượt này đã ĐĂNG
    KÝ trước khi gọi: lượt thứ ba tới lúc ta còn chờ sẽ huỷ ta, không huỷ lại
    lượt đầu."""
    if previous is not None and previous is not me and not previous.done():
        previous.cancel()
        await asyncio.wait({previous}, timeout=SUPERSEDE_WAIT_SECONDS)


async def _close_sessions(db: AsyncIOMotorDatabase, ids: dict, *, reason: str,
                          keep: Optional[str] = None) -> None:
    """Đóng phiên chờ mở / đổi trong lượt — khách không nghe câu hỏi của chúng.
    `keep`: phiên mà câu mẫu code đã hỏi câu hỏi của nó (Ruling R12) — giữ mở."""
    try:
        closed = await _shielded(WaitingSessionService(db).close_opened_in_turn(
            ids["user_id"], ids["turn_id"], reason=reason, keep=keep))
    except Exception as exc:
        logger.warning("sessions_close_failed", extra={**ids, "error_type": type(exc).__name__})
        return
    if closed:
        logger.info("sessions_closed", extra={**ids, "agents": closed, "reason": reason})


def _segment(messages: list[BaseMessage], opener_id: str) -> list[BaseMessage]:
    """Phần thread của lượt này (từ message mở lượt); rỗng nếu nó chưa được ghi."""
    for index, message in enumerate(messages):
        if message.id == opener_id:
            return messages[index:]
    return []


def _same_turn(state: dict, turn_id: str) -> bool:
    """Khoá theo lượt trong state là của lượt NÀY (lỗi trước `before_agent` thì
    chúng vẫn là của lượt trước)."""
    return state.get("turn_id") == turn_id


# ---------------------------------------------------------------------------
# Câu ghi lại việc bấm nút (tiếng Anh — model đọc ở lượt sau)
# ---------------------------------------------------------------------------


def _pressed(decision: str) -> str:
    return "Agree" if decision == "yes" else "Decline"


def button_note(outcome: PendingOutcome, reply: str, *, pressed: Literal["yes", "no"],
                continue_next: bool) -> str:
    """HumanMessage do code dựng cho lượt nút bấm: khách bấm nút gì cho đề xuất
    nào, kết quả, câu khách đã được báo. `continue_next`: Orchestrator làm tiếp
    yêu cầu kế của phiên Đặt lịch (spec §5.1)."""
    note = (
        f'Button press (written by code, not typed by the customer): the customer pressed '
        f'"{_pressed(pressed)}" on the pending {outcome.kind} proposal {outcome.proposal_id} '
        f"for {outcome.at.spoken} ({outcome.at.iso}). Result: {outcome.outcome}. "
        f"The customer has already been told: {reply}"
    )
    if continue_next:
        note += (
            "\nNow continue with the next request in the waiting appointment session "
            "(appointment_task with continue_session=true), then ask the customer to confirm "
            "the new proposal. Do not repeat what the customer has already been told."
        )
    return note


def _stale_note(pressed: Literal["yes", "no"], reply: str) -> str:
    # Không chép `proposal_id` của client vào thread — chuỗi đó không đáng tin.
    return (
        f'Button press (written by code, not typed by the customer): the customer pressed '
        f'"{_pressed(pressed)}" on a proposal that is no longer pending (expired, replaced or '
        f"already decided). Nothing was changed. The customer has already been told: {reply}"
    )


# ---------------------------------------------------------------------------
# Chạy Orchestrator
# ---------------------------------------------------------------------------


async def _orchestrate(turn: _Turn, message: HumanMessage,
                       context: SalonContext) -> AsyncIterator[AgentEvent]:
    """Chạy Orchestrator trong trần `orch_orchestrate_timeout_seconds`, chỉ phát
    sự kiện tool có nhãn. Trần chỉ bọc lúc CHỜ graph, không bọc `yield`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.orch_orchestrate_timeout_seconds
    stream = turn.agent.astream_events({"messages": [message]}, config=turn.config,
                                       context=context, version="v2")
    try:
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    raw = await anext(stream)
            except StopAsyncIteration:
                return
            event = tool_event(raw)
            if event is not None:
                yield event
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()


async def _state(turn: _Turn) -> dict:
    return dict((await turn.agent.aget_state(turn.config)).values or {})


async def _final_answer(turn: _Turn, opener: HumanMessage) -> _Answer:
    """Câu trả lời = AIMessage cuối (không tool call) của lượt này trong state."""
    state = await _state(turn)
    segment = _segment(list(state.get("messages", [])), opener.id)
    reply = segment[-1] if segment else None
    if not isinstance(reply, AIMessage) or has_calls(reply) or not reply.text.strip():
        raise RuntimeError("turn_ended_without_reply")
    code = reply.additional_kwargs.get("salon_source") == "code"
    same = _same_turn(state, turn.turn_id)
    facts = read_facts(state) if same else []
    announced = list(state.get("announced_turn_ids", [])) if same and not code else []
    return _Answer(reply.text, "code" if code else "llm", "fallback" if code else "answer", facts,
                   announced)


def _log_failure(turn: _Turn, exc: Exception) -> None:
    extra = {**turn.ids, "error_type": type(exc).__name__}
    if isinstance(exc, TimeoutError):
        logger.warning("orchestrator_timeout",
                       extra={**extra, "seconds": settings.orch_orchestrate_timeout_seconds})
    elif isinstance(exc, (*_LIMIT_ERRORS, LlmBudgetExceeded)):
        logger.warning("orchestrator_limit", extra=extra)
    else:
        logger.error("orchestrator_failed", extra=extra, exc_info=True)


async def _reply_facts(turn: _Turn, state: dict, facts: list[TurnFact],
                       decision: Optional[PendingOutcome], question: str) -> ReplyFacts:
    """Dữ kiện câu mẫu từ state đã lưu + ledger, bối cảnh đọc lại (DB có thể vừa đổi)."""
    same = _same_turn(state, turn.turn_id)
    turn.holder.mark_stale()
    return await build_reply_facts(
        turn.db, snapshot=await turn.holder.current(), turn_facts=facts, turn_id=turn.turn_id,
        turn_kind=turn.kind,
        pending_outcome=(read_pending_outcome(state) if same else None) or decision,
        question=question, previous_replies=previous_replies(list(state.get("messages", []))),
        pending_decided=bool(state.get("pending_decided")) if same else decision is not None,
    )


async def _write_code_reply(turn: _Turn, messages: list[BaseMessage], opener: HumanMessage,
                            text: str) -> None:
    """MỘT `aupdate_state`: xoá tool call dở (và ToolMessage lẻ, lời nhắc guard,
    câu chưa qua guard) của lượt này, thêm câu mẫu code. Message mở lượt chưa
    kịp vào thread thì thêm nó trước."""
    segment = _segment(messages, opener.id)
    updates: list[BaseMessage] = []
    if segment:
        drop = set(broken_message_ids(segment))
        drop |= {m.id for m in segment if isinstance(m, AIMessage) and not has_calls(m) and m.id}
        updates += [RemoveMessage(id=m.id) for m in segment if m.id in drop]
    else:
        updates.append(opener)
    updates.append(AIMessage(content=text, additional_kwargs=dict(CODE_SOURCE)))
    # Finding 5: xoá luôn `structured_response` — model node có thể đã ghi nó
    # trước khi guard ném lỗi (ví dụ `check_structured` lỗi sau một JSON hợp
    # lệ), và không nhánh nào khác dọn lại khoá này cho lượt rơi về câu mẫu.
    await turn.agent.aupdate_state(
        turn.config, {"messages": updates, "structured_response": None}, as_node="model")


async def _fallback(turn: _Turn, opener: HumanMessage, *, question: str,
                    limit: bool) -> Optional[_Answer]:
    """Câu mẫu code sau lỗi (spec §8). Lượt chưa làm được gì (không dữ kiện,
    không quyết định, không chạm trần) → `None` = `TURN_ERROR_MESSAGE`."""
    state = await _state(turn)
    messages = list(state.get("messages", []))
    facts = read_facts(state) if _same_turn(state, turn.turn_id) else []
    record = await turn.ledger.record(turn.turn_id)
    decision = record.decision if record is not None else None
    if not (limit or facts or decision is not None):
        return None
    reply_facts = await _reply_facts(turn, state, facts, decision, question)
    text = fallback_reply(reply_facts)
    await _close_sessions(turn.db, turn.ids, reason="fallback", keep=reply_facts.asked_session)
    await _shielded(_write_code_reply(turn, messages, opener, text))
    return _Answer(text, "code", "limit" if limit else "fallback", facts)


# ---------------------------------------------------------------------------
# Hai nhánh của lượt
# ---------------------------------------------------------------------------


async def _typed_turn(turn: _Turn, text: str, box: _Box) -> AsyncIterator[AgentEvent]:
    opener = HumanMessage(content=text, id=turn.opener_id)
    try:
        async for event in _orchestrate(turn, opener, turn.context(question=text, preset=None)):
            yield event
        box.answer = await _final_answer(turn, opener)
    except Exception as exc:
        _log_failure(turn, exc)
        box.answer = await _fallback(turn, opener, question=text,
                                     limit=isinstance(exc, _LIMIT_ERRORS))


async def _write_button_thread(turn: _Turn, note: str, reply: str) -> None:
    """Lượt nút không gọi model: MỘT `aupdate_state` ghi việc bấm nút + câu báo,
    để lượt gõ chữ sau Orchestrator biết chuyện đã xảy ra."""
    await turn.agent.aupdate_state(turn.config, {"messages": [
        HumanMessage(content=note, id=turn.opener_id, additional_kwargs={BUTTON_MARK: True}),
        AIMessage(content=reply, additional_kwargs=dict(CODE_SOURCE)),
    ]}, as_node="model")


async def _has_next_request(holder: SnapshotHolder) -> bool:
    session = (await holder.current()).sessions.get("appointment")
    return bool(session is not None and session.memory.get("next_requests"))


async def _continue_next(turn: _Turn, button: ButtonReply, outcome: PendingOutcome,
                         reply: str, box: _Box) -> AsyncIterator[AgentEvent]:
    """Ngoại lệ duy nhất của nút bấm (spec §3.2, §5.1): vừa duyệt mà phiên Đặt
    lịch còn yêu cầu kế → Orchestrator đề xuất lịch kế. Câu tới khách = câu báo
    của code + câu của Orchestrator; Orchestrator lỗi thì chỉ câu báo, phiên
    vẫn chờ (không đóng)."""
    # Câu báo của code đã nói giờ vừa đặt: đánh dấu ĐÃ BÁO trước khi chạy
    # Orchestrator, để guard không bắt nó nhắc lại (khối Unannounced / R0).
    # Bị huỷ trước `complete` → `run_turn` hoàn lại dấu này (Ruling R14).
    turn.progress.premarked = True
    await turn.ledger.mark_announced(turn.turn_id)
    turn.holder.mark_stale()
    opener = HumanMessage(
        content=button_note(outcome, reply, pressed=button.decision, continue_next=True),
        id=turn.opener_id, additional_kwargs={BUTTON_MARK: True})
    try:
        async for event in _orchestrate(turn, opener, turn.context(question="", preset=outcome)):
            yield event
        answer = await _final_answer(turn, opener)
        box.answer = _Answer(f"{reply} {answer.text}", answer.source, "button", answer.facts,
                             answer.announced)
        return
    except Exception as exc:
        _log_failure(turn, exc)
    state = await _state(turn)
    facts = read_facts(state) if _same_turn(state, turn.turn_id) else []
    text = reply
    if any(isinstance(fact, ProposalFact) for fact in facts):
        # Lượt đã tạo đề xuất mới (nút sẽ hiện) — câu phải hỏi khách về nó.
        record = await turn.ledger.record(turn.turn_id)
        decision = record.decision if record is not None else None
        text = f"{reply} {fallback_reply(await _reply_facts(turn, state, facts, decision, ''))}"
    await _shielded(_write_code_reply(turn, list(state.get("messages", [])), opener, text))
    box.answer = _Answer(text, "code", "button", facts)


async def _button_turn(turn: _Turn, button: ButtonReply, box: _Box) -> AsyncIterator[AgentEvent]:
    """Nút bấm = API bằng code (chủ dự án 09/11): kiểm nút, `apply_decision`, câu
    báo do code viết, không gọi model."""
    snapshot = await turn.holder.current()
    address = snapshot.customer.address_phrase
    pending = snapshot.pending          # chỉ đề xuất CỦA KHÁCH, còn `pending`, còn hạn
    if pending is None or pending.id != button.proposal_id:
        reply = BUTTON_STALE_REPLY.format(address=address)
        logger.info("button_stale", extra=turn.ids)
        await _shielded(_write_button_thread(turn, _stale_note(button.decision, reply), reply))
        box.answer = _Answer(reply, "code", "stale_button")
        return

    outcome: PendingOutcome = await _shielded(apply_decision(
        turn.db, turn.user, pending=pending,
        label="approve" if button.decision == "yes" else "reject",
        turn_id=turn.turn_id, asked_at=turn.asked_at, ledger=turn.ledger,
        thread_id=turn.thread_id,
    ))
    turn.holder.mark_stale()
    reply = button_reply(outcome, address=address, shop_phone=settings.shop_phone)
    logger.info("button_decided", extra={**turn.ids, "outcome": outcome.outcome})

    if outcome.outcome == "approved" and await _has_next_request(turn.holder):
        async for event in _continue_next(turn, button, outcome, reply, box):
            yield event
        return

    note = button_note(outcome, reply, pressed=button.decision, continue_next=False)
    await _shielded(_write_button_thread(turn, note, reply))
    box.answer = _Answer(reply, "code", "button")


# ---------------------------------------------------------------------------
# Sau câu trả lời
# ---------------------------------------------------------------------------


async def _record(turn: _Turn, answer: _Answer, customer_text: str) -> None:
    """Ledger + nhật ký `conversations` — mất một dòng sổ sách không được biến
    một lượt đã có câu trả lời thành `error`."""
    try:
        await turn.ledger.add_facts(turn.turn_id, answer.facts)
        await turn.ledger.finish(turn.turn_id, ended_by=answer.ended_by,
                                 answer_source=answer.source)
    except Exception as exc:
        logger.warning("ledger_finish_failed", extra={**turn.ids, "error_type": type(exc).__name__})
    try:
        conversations = ConversationService(turn.db)
        user_id = str(turn.user.id)
        await conversations.append(user_id, "user", customer_text, created_at=turn.asked_at)
        await conversations.append(user_id, "assistant", answer.text, source=answer.source,
                                   created_at=max(now_ms(), turn.asked_at))
    except Exception as exc:
        logger.error("conversations_persist_failed",
                     extra={**turn.ids, "error_type": type(exc).__name__})


async def _seed_self_address(turn: _Turn, text: str) -> None:
    """Khách CHƯA có `self_address` mà câu gõ chữ này tự xưng "chị"/"anh" → ghi
    MỘT lần (CONTEXT.md "Xưng hô", Ruling R13). `set_self_address` tự lọc
    `self_address: None` nên lượt chồng nhau / giá trị admin sửa tay không bị
    ghi đè. Chỉ lượt gõ chữ — nút bấm không phải lời khách tự xưng. Lỗi chỉ
    log id, không bao giờ log chữ khách."""
    if turn.user.self_address is not None:
        return
    try:
        self_address = detect_self_address(text)
        if self_address:
            await UserRepository(turn.db).set_self_address(str(turn.user.id), self_address)
    except Exception as exc:
        logger.warning("self_address_seed_failed",
                       extra={**turn.ids, "error_type": type(exc).__name__})


async def _mark_announced(turn: _Turn, answer: _Answer, r0: Optional[UnannouncedDecision]) -> None:
    """Đánh dấu ĐÃ BÁO các quyết định câu khách vừa nhận đã nêu.

    - Câu của model (`source == "llm"`): đúng các `turn_id` guard ghi khi nhận
      câu (`answer.announced` — quyết định / R0 được trích dẫn với vai trò
      booked / moved / cancelled / failed), dù câu nói giờ ngắn gọn.
    - Câu mẫu code: không có đoạn trích dẫn nhưng luôn chứa nguyên `spoken` —
      so nguyên văn giờ của quyết định của lượt và của R0."""
    try:
        if answer.source == "llm":
            for turn_id in answer.announced:
                await turn.ledger.mark_announced(turn_id)
            return
        said = normalize_for_match(answer.text)
        record = await turn.ledger.record(turn.turn_id)
        decision = record.decision if record is not None else None
        if (decision is not None and not record.announced
                and decision.outcome in ("approved", "failed")
                and normalize_for_match(decision.at.spoken) in said):
            await turn.ledger.mark_announced(turn.turn_id)
        if (r0 is not None and r0.turn_id != turn.turn_id
                and normalize_for_match(r0.at.spoken) in said):
            await turn.ledger.mark_announced(r0.turn_id)
    except Exception as exc:
        logger.warning("mark_announced_failed", extra={**turn.ids, "error_type": type(exc).__name__})


async def _unmark_announced(db: AsyncIOMotorDatabase, ids: dict) -> None:
    """Câu báo "đã đặt xong" của nút bấm không tới khách — R0 báo lại ở lượt sau."""
    try:
        await _shielded(TurnLedger(db).unmark_announced(ids["turn_id"]))
    except Exception as exc:
        logger.warning("unmark_announced_failed", extra={**ids, "error_type": type(exc).__name__})


async def _end_with_error(turn: _Turn) -> None:
    await _close_sessions(turn.db, turn.ids, reason="fallback")
    try:
        await _shielded(turn.ledger.finish(turn.turn_id, ended_by="error", answer_source="code"))
    except Exception as exc:
        logger.warning("ledger_finish_failed", extra={**turn.ids, "error_type": type(exc).__name__})


async def _proposal_payload(db: AsyncIOMotorDatabase, user: User, ids: dict) -> Optional[dict]:
    """Nút Đồng ý/Không: đề xuất `pending` CÒN HẠN mới nhất, đọc lại từ DB. Lỗi
    đọc chỉ đáng mất nút, không mất cả lượt."""
    try:
        proposal = await ProposalService(db).latest(user)
    except Exception as exc:
        logger.warning("pending_proposal_payload_failed",
                       extra={**ids, "error_type": type(exc).__name__})
        return None
    if proposal is None or proposal.status != "pending" or proposal.expires_at <= now_utc():
        return None
    return {
        "id": str(proposal.id),
        "kind": proposal.kind,
        "expires_at": to_local(proposal.expires_at).isoformat(),
        "when_spoken": format_vi_datetime(proposal.start_at),
    }


async def _consolidate(db: AsyncIOMotorDatabase, user: User, previous_thread_id: str,
                       now: datetime, ids: dict) -> None:
    """Lượt đầu của cuộc mới: gom bộ nhớ dài hạn từ cuộc trước (spec §3.5).
    Lỗi / quá giờ chỉ log — lượt vẫn chạy với bộ nhớ cũ. Shield: lượt bị lượt
    mới thay giữa chừng thì việc gom vẫn chạy xong ở nền — lượt sau cùng cuộc
    không gom lại nữa (`created` đã là False)."""
    try:
        await _shielded(asyncio.wait_for(
            consolidate_memory(db, get_store(), user, previous_thread_id=previous_thread_id,
                               now=now, turn_id=ids["turn_id"]),
            timeout=settings.salon_memory_timeout_seconds,
        ))
    except Exception as exc:
        logger.warning("memory_consolidate_failed", extra={
            **ids, "previous_thread_id": previous_thread_id, "error_type": type(exc).__name__})


# ---------------------------------------------------------------------------
# run_turn
# ---------------------------------------------------------------------------


async def run_turn(db: AsyncIOMotorDatabase, user: User,
                   question: "str | ButtonReply") -> AsyncIterator[AgentEvent]:
    """Một lượt chat. Phát `turn_started`, `tool_started` / `tool_finished`, rồi
    ĐÚNG MỘT `complete {"answer", "proposal"}` hoặc `error {"message"}`. Không
    có `token`."""
    user_id = str(user.id)
    me = asyncio.current_task()
    previous = _ACTIVE_TURNS.get(user_id)
    if me is not None:
        _ACTIVE_TURNS[user_id] = me          # đăng ký TRƯỚC khi huỷ / chờ lượt cũ
    progress = _Progress(user_id=user_id)
    try:
        await _supersede(me, previous)
        async for event in _run_turn(db, user, question, progress):
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        if progress.turn_id is not None and not progress.completed:
            await _close_sessions(db, progress.ids(), reason="superseded")
            if progress.premarked:
                await _unmark_announced(db, progress.ids())
        superseded = _ACTIVE_TURNS.get(user_id) is not me
        logger.info("turn_superseded" if superseded else "turn_cancelled", extra=progress.ids())
        raise
    finally:
        if me is not None and _ACTIVE_TURNS.get(user_id) is me:
            del _ACTIVE_TURNS[user_id]


async def _run_turn(db: AsyncIOMotorDatabase, user: User, question: "str | ButtonReply",
                    progress: _Progress) -> AsyncIterator[AgentEvent]:
    yield AgentEvent("turn_started")
    turn: Optional[_Turn] = None
    try:
        button = question if isinstance(question, ButtonReply) else None
        if button is not None and button.decision not in BUTTON_LABELS:
            raise ValueError("bad_button_decision")
        if button is None and not isinstance(question, str):
            raise TypeError("bad_question_type")
        text = "" if button is not None else (single_line(question, CUSTOMER_TEXT_MAX_CHARS) or "")

        asked_at = now_ms()
        turn_id = uuid.uuid4().hex
        progress.turn_id = turn_id
        thread, created = await ConversationThreadService(db).current(progress.user_id, asked_at)
        thread_id = str(thread.id)
        progress.thread_id = thread_id
        if created and thread.previous_id:
            await _consolidate(db, user, thread.previous_id, asked_at, progress.ids())

        ledger = TurnLedger(db)
        kind: Literal["typed", "button"] = "button" if button is not None else "typed"
        await ledger.start(turn_id=turn_id, user_id=progress.user_id, thread_id=thread_id,
                           asked_at=asked_at, kind=kind)
        holder = SnapshotHolder(db, user, asked_at=asked_at, thread_id=thread_id, services=ledger)
        # Quyết định chưa báo từ lượt trước (R0) — đọc ở đầu lượt, trước khi
        # quyết định của lượt này (nếu có) thành "mới nhất".
        r0 = (await holder.current()).unannounced
        agent = build_orchestrator(db, user, holder)
        turn = _Turn(
            db=db, user=user, turn_id=turn_id, thread_id=thread_id, asked_at=asked_at,
            kind=kind, ledger=ledger, holder=holder, agent=agent,
            progress=progress,
            config={
                "configurable": {"thread_id": thread_id},
                # Khoá cấp cao nhất, không nằm trong `configurable` (docs LangGraph).
                "recursion_limit": recursion_limit(agent),
                "callbacks": get_callbacks(),
                "metadata": get_trace_metadata(user_id=progress.user_id, conversation_id=thread_id),
            },
        )
        logger.info("turn_started", extra={**turn.ids, "kind": kind, "new_conversation": created})

        box = _Box()
        branch = _button_turn(turn, button, box) if button is not None else _typed_turn(turn, text, box)
        async for event in branch:
            yield event
        answer = box.answer

        if answer is None:
            await _end_with_error(turn)
            yield AgentEvent("error", {"message": TURN_ERROR_MESSAGE})
            return

        customer_text = BUTTON_LABELS[button.decision] if button is not None else text
        await _shielded(_record(turn, answer, customer_text))
        if button is None:
            await _shielded(_seed_self_address(turn, text))
        proposal = await _proposal_payload(db, user, turn.ids)
        progress.completed = True
        logger.info("turn_finished", extra={**turn.ids, "ended_by": answer.ended_by,
                                            "answer_source": answer.source})
        yield AgentEvent("complete", {"answer": answer.text, "proposal": proposal})
        await _shielded(_mark_announced(turn, answer, r0))
    except Exception as exc:
        logger.error("agent_turn_failed", extra={**progress.ids(), "error_type": type(exc).__name__},
                     exc_info=True)
        if progress.completed:
            return
        if turn is not None:
            await _end_with_error(turn)
        if progress.premarked:
            await _unmark_announced(db, progress.ids())
        yield AgentEvent("error", {"message": TURN_ERROR_MESSAGE})
