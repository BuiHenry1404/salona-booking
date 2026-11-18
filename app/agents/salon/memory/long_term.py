"""Bộ nhớ dài hạn về khách (spec §3.5) — ba mục cố định trong namespace
`("memories", user_id)` của store (`memory/store.py`): `preferences`, `notes`,
`last_visit`. Đọc mỗi lượt bởi `ContextMiddleware` (vào khối bối cảnh, dưới
nhãn `Customer memory`); ghi DUY NHẤT bởi `consolidate_memory` — Orchestrator
không có tool ghi memory, nên chữ khách không bao giờ vào thẳng store.

Không tìm kiếm ngữ nghĩa (không embedding/vector index — spec §3.5): mỗi
khách chỉ vài mục nhỏ, đọc cả ba khoá bằng `aget`, không `asearch`.

`memory/` KHÔNG import từ `orchestrator/` (dù `orchestrator/middleware`
import ngược lại từ `memory/`): `_is_summary`/`_is_final_reply` dưới đây lặp
lại hai điều kiện nhỏ của `orchestrator/middleware/turns.py` thay vì import,
để gói này không phụ thuộc Orchestrator.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.store.base import BaseStore
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agents.checkpoint import get_checkpointer
from app.agents.llm import build_chat_model
from app.agents.salon.memory.prompts import MEMORY_PROMPT
from app.agents.salon.shared.text_scan import scan_money, scan_spoken_clocks, scan_times, words
from app.agents.salon.shared.tool_names import CONTROL_TOOLS, SUBAGENT_TOOLS
from app.core.clock import to_local
from app.core.config import settings
from app.core.logging import get_logger
from app.core.text import single_line
from app.models.user import User

logger = get_logger(__name__)

MEMORY_NAMESPACE = "memories"

# "Tóm tắt + 6 message khách/bot cuối" (task brief) — đủ cho model viết bộ
# nhớ mà không phải đọc lại cả cuộc trò chuyện trước.
_RECENT_MESSAGE_COUNT = 6
_SUMMARY_SOURCE = "summarization"

# Tên tool nội bộ — một dòng memory nhắc tới tên này gần như chắc chắn là
# model đang chép lại một lời gọi tool thay vì viết sự thật về khách. MỘT
# nguồn duy nhất (`shared/tool_names.py`), không chép tay — review round 1:
# một bản chép tay đã lạc hậu, thiếu `parse_time`.
_TOOL_NAMES = CONTROL_TOOLS | SUBAGENT_TOOLS
_HEX_DIGITS = frozenset("0123456789abcdef")


class LastVisit(BaseModel):
    day: date
    lines: list[str] = Field(default_factory=list)


class CustomerMemory(BaseModel):
    preferences: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    last_visit: Optional[LastVisit] = None


# ---------------------------------------------------------------------------
# sanitize_memory — hàng rào trước khi ghi (spec §3.5)
# ---------------------------------------------------------------------------


def _looks_like_id(token: str) -> bool:
    """24 ký tự hex liền nhau — hình dạng `ObjectId` Mongo. So bằng `str`
    method (Global Constraints: không regex trong `app/agents/salon`): đúng
    độ dài và mọi ký tự nằm trong tập hex."""
    return len(token) == 24 and set(token.lower()) <= _HEX_DIGITS


def _is_safe(line: str) -> bool:
    """Một dòng memory không được mang giờ / tiền / id / tên tool — những
    thứ đó luôn đọc lại từ DB mỗi lượt, không bao giờ là sự thật đóng băng
    trong bộ nhớ dài hạn (spec §3.5)."""
    if scan_times(line) or scan_spoken_clocks(line) or scan_money(line):
        return False
    if any(name in line.lower() for name in _TOOL_NAMES):
        return False
    if any(_looks_like_id(token) for token in words(line)):
        return False
    return True


def _clean_lines(raw: Sequence[str], *, max_chars: int) -> list[str]:
    out: list[str] = []
    for item in raw:
        line = single_line(item, max_chars)
        if line is not None and _is_safe(line):
            out.append(line)
    return out


def _total_chars(*groups: Sequence[str]) -> int:
    return sum(len(item) for group in groups for item in group)


def sanitize_memory(memory: CustomerMemory, *, max_chars: int) -> CustomerMemory:
    """Hàng rào của bộ nhớ dài hạn (spec §3.5, task brief): bỏ mục mang giờ /
    tiền / id / tên tool, mỗi mục qua `single_line`, rồi cắt bớt MỤC CUỐI
    (lần lượt `last_visit`, `notes`, `preferences` — đúng thứ tự khai trong
    `CustomerMemory`, mục khai sau bị cắt trước) tới khi tổng ký tự của cả ba
    mục ≤ `max_chars`."""
    preferences = _clean_lines(memory.preferences, max_chars=max_chars)
    notes = _clean_lines(memory.notes, max_chars=max_chars)
    visit_lines = (
        _clean_lines(memory.last_visit.lines, max_chars=max_chars)
        if memory.last_visit is not None else []
    )

    while _total_chars(preferences, notes, visit_lines) > max_chars:
        if visit_lines:
            visit_lines.pop()
        elif notes:
            notes.pop()
        elif preferences:
            preferences.pop()
        else:
            break

    last_visit = (
        LastVisit(day=memory.last_visit.day, lines=visit_lines)
        if memory.last_visit is not None and visit_lines else None
    )
    return CustomerMemory(preferences=preferences, notes=notes, last_visit=last_visit)


# ---------------------------------------------------------------------------
# read_memory / render_memory
# ---------------------------------------------------------------------------


async def read_memory(store: BaseStore, user_id: str) -> CustomerMemory:
    """Đọc ba mục cố định của namespace `("memories", user_id)` — khách mới,
    chưa từng được gom, trả về bộ nhớ rỗng (không phải lỗi).

    FAIL-SOFT (review round 1, controller re-chấm): một bản ghi hỏng (vd
    `last_visit.day` không parse được thành `date`) không được làm hỏng cả
    lượt của khách đó — mọi lỗi đọc/parse chỉ log `memory_read_failed` và trả
    bộ nhớ rỗng, để `ContextMiddleware` vẫn dựng được khối bối cảnh."""
    namespace = (MEMORY_NAMESPACE, user_id)
    try:
        preferences_item = await store.aget(namespace, "preferences")
        notes_item = await store.aget(namespace, "notes")
        last_visit_item = await store.aget(namespace, "last_visit")

        last_visit: Optional[LastVisit] = None
        if last_visit_item is not None:
            data = last_visit_item.value.get("data") or {}
            if data.get("day") and data.get("lines"):
                last_visit = LastVisit(day=data["day"], lines=list(data["lines"]))

        return CustomerMemory(
            preferences=list((preferences_item.value.get("data") if preferences_item else None) or []),
            notes=list((notes_item.value.get("data") if notes_item else None) or []),
            last_visit=last_visit,
        )
    except Exception as exc:
        logger.warning("memory_read_failed", extra={
            "user_id": user_id, "error": str(exc), "error_type": type(exc).__name__,
        })
        return CustomerMemory()


def render_memory(memory: CustomerMemory) -> Optional[str]:
    """Chữ bộ nhớ dài hạn cho khối bối cảnh (`shared/context.py::render_context`,
    nhãn `Customer memory`) — `None` khi khách chưa có gì, để khối bối cảnh bỏ
    hẳn dòng đó thay vì in một nhãn rỗng."""
    if not memory.preferences and not memory.notes and memory.last_visit is None:
        return None
    parts: list[str] = []
    if memory.preferences:
        parts.append("Preferences: " + "; ".join(memory.preferences))
    if memory.notes:
        parts.append("Notes: " + "; ".join(memory.notes))
    if memory.last_visit is not None:
        parts.append(
            f"Last visit ({memory.last_visit.day.isoformat()}): "
            + "; ".join(memory.last_visit.lines)
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# consolidate_memory — bước gom (spec §3.5)
# ---------------------------------------------------------------------------


def _is_summary(message: BaseMessage) -> bool:
    return (isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == _SUMMARY_SOURCE)


def _is_final_reply(message: BaseMessage) -> bool:
    return isinstance(message, AIMessage) and not (message.tool_calls or message.invalid_tool_calls)


def _relevant_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Bản tóm tắt (nếu có) + `_RECENT_MESSAGE_COUNT` message khách/bot cuối.
    Bỏ ToolMessage và AIMessage có tool call: chúng mang id/giờ thô của một
    lần gọi tool, đúng thứ `consolidate_memory` không bao giờ được chép lại
    làm sự thật của bộ nhớ dài hạn."""
    summary = next((m for m in messages if _is_summary(m)), None)
    talk = [m for m in messages
            if (isinstance(m, HumanMessage) and not _is_summary(m)) or _is_final_reply(m)]
    recent = talk[-_RECENT_MESSAGE_COUNT:]
    return ([summary] if summary is not None else []) + recent


def _transcript(messages: Sequence[BaseMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        if _is_summary(message):
            lines.append(f"summary: {single_line(message.text, 1200) or ''}")
        else:
            role = "customer" if isinstance(message, HumanMessage) else "salon"
            lines.append(f"{role}: {single_line(message.text, 400) or ''}")
    return "\n".join(lines)


def _format_group(items: Sequence[str]) -> str:
    return "; ".join(items) or "(none)"


async def consolidate_memory(
    db: AsyncIOMotorDatabase, store: BaseStore, user: User, *,
    previous_thread_id: str, now: datetime, turn_id: Optional[str] = None,
) -> bool:
    """Bước gom bộ nhớ dài hạn (spec §3.5): chạy ở lượt ĐẦU TIÊN của một cuộc
    trò chuyện MỚI, TRƯỚC khi Orchestrator chạy, trên thread CŨ vừa đóng lại
    (`ConversationThread.previous_id`). `db` không được đọc trực tiếp ở đây —
    mọi thứ cần đều tới từ checkpoint của thread cũ và store — tham số giữ lại
    để chữ ký đồng dạng với các bước đầu lượt khác của gói, phòng khi một bản
    sau cần tra thêm sự thật nghiệp vụ. `turn_id` (lượt ĐANG chạy, gọi bước
    gom này — không phải lượt của thread cũ) chỉ vào log, cho đúng bộ ba
    `user_id`/`turn_id`/`thread_id` mọi log có cấu trúc của gói phải mang.

    Không bao giờ ném: mọi lỗi (model lỗi, quá timeout, schema sai, VÀ lỗi
    lúc ghi ba khoá — review round 1: giai đoạn ghi từng nằm NGOÀI try) chỉ
    log `memory_consolidate_failed` và trả `False` — lượt hiện tại vẫn chạy
    với bộ nhớ cũ.

    Giới hạn đã biết: checkpoint có TTL `checkpoint_ttl_days` (mặc định 7
    ngày, `app/agents/checkpoint.py`). Một cuộc trò chuyện trước cách đây hơn
    ngần ấy ngày không còn checkpoint để đọc — `aget_tuple` trả `None`, hàm
    này trả `False` mà không log lỗi (biết trước, không phải sự cố), và
    `last_visit` giữ nguyên bản ghi cũ (chấp nhận, spec §3.5).
    """
    user_id = str(user.id)
    log_extra = {"user_id": user_id, "turn_id": turn_id, "thread_id": previous_thread_id}
    try:
        tuple_ = await get_checkpointer().aget_tuple(
            {"configurable": {"thread_id": previous_thread_id}})
        if tuple_ is None:
            return False
        messages = tuple_.checkpoint.get("channel_values", {}).get("messages", [])
        relevant = _relevant_messages(messages)
        if not relevant:
            return False

        previous = await read_memory(store, user_id)
        ts = tuple_.checkpoint.get("ts")
        conversation_day = to_local(datetime.fromisoformat(ts)).date() if ts else to_local(now).date()
        prompt = MEMORY_PROMPT.format(
            today=to_local(now).date().isoformat(),
            conversation_day=conversation_day.isoformat(),
            preferences=_format_group(previous.preferences),
            notes=_format_group(previous.notes),
            last_visit=(
                f"{previous.last_visit.day.isoformat()}: {_format_group(previous.last_visit.lines)}"
                if previous.last_visit is not None else "(none)"
            ),
            messages=_transcript(relevant),
        )
        model = build_chat_model(tags=["memory"], streaming=False)
        result = await asyncio.wait_for(
            model.with_structured_output(CustomerMemory).ainvoke(prompt),
            timeout=settings.salon_memory_timeout_seconds,
        )

        cleaned = sanitize_memory(result, max_chars=settings.salon_memory_max_chars)
        namespace = (MEMORY_NAMESPACE, user_id)
        await store.aput(namespace, "preferences",
                         {"data": cleaned.preferences, "updated_at": now.isoformat()})
        await store.aput(namespace, "notes",
                         {"data": cleaned.notes, "updated_at": now.isoformat()})
        if cleaned.last_visit is not None:
            await store.aput(namespace, "last_visit", {
                "data": {"day": cleaned.last_visit.day.isoformat(), "lines": cleaned.last_visit.lines},
                "updated_at": now.isoformat(),
            })
    except Exception as exc:
        logger.warning("memory_consolidate_failed", extra={
            **log_extra, "error": str(exc), "error_type": type(exc).__name__,
        })
        return False
    return True
