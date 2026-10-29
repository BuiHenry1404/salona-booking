"""Đọc câu khách trả lời câu hỏi xác nhận: ủy thác hoàn toàn cho `TurnAnalysis`.

Toàn bộ các bộ từ điển từ khóa chuỗi tĩnh (_NEG_PHRASES_*, _GO_VERBS, veto_reason)
đã được loại bỏ. Quyết định xác nhận được giao trực tiếp cho bộ phân tích ngữ nghĩa có cấu trúc (`label_fn`),
với cơ chế fail-closed an toàn (lỗi/không rõ -> 'unclear').
"""
import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal, Mapping, Optional, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.booking_graph import tokens
from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.prompts import (CLASSIFY_GO_AHEAD, CLASSIFY_KIND,
                                              CLASSIFY_PROMPT, CLASSIFY_QUEUE)
from app.agents.booking_graph.timeparse import _to_24h
from app.agents.llm import build_chat_model
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.text import strip_diacritics  # noqa: F401 (re-export)

logger = get_logger(__name__)

_PERIOD_FROM_BARE = {"sang": "sáng", "trua": "trưa", "chieu": "chiều", "toi": "tối"}


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _hour_differs(raw_hour: int, period: Optional[str], start_hour: int) -> bool:
    hour = _to_24h(raw_hour, period) if period else None
    if hour is not None:
        return hour != start_hour
    return raw_hour % 12 != start_hour % 12


def _structured_time_differs(item: Any, local_start: datetime, local_now: datetime) -> bool:
    get = item.get if isinstance(item, Mapping) else lambda k: getattr(item, k, None)
    hour, minute, part = get("hour"), get("minute"), get("part_of_day")
    weekday, day, month, offset = get("weekday"), get("day"), get("month"), get("day_offset")
    period = _PERIOD_FROM_BARE.get(part) if part else None
    if hour is not None and _hour_differs(hour, period, local_start.hour):
        return True
    if minute is not None and minute != local_start.minute:
        return True
    if offset is not None:
        if offset == -1 or local_now.date().toordinal() + offset != local_start.date().toordinal():
            return True
    if weekday is not None and weekday != local_start.weekday():
        return True
    if day is not None and day != local_start.day:
        return True
    if month is not None and month != local_start.month:
        return True
    return False


def mentions_other_time(text: str, start: datetime, now: datetime,
                        *, mentioned_times: Optional[Sequence[Any]] = None) -> bool:
    """Khách nhắc một giờ, một ngày hay một thứ KHÁC với đề xuất."""
    local_start, local_now = to_local(_as_datetime(start)), to_local(now)
    for hour, period, minute in tokens.scan_times(text):
        if _hour_differs(hour, period, local_start.hour):
            return True
        if minute is not None and minute != local_start.minute:
            return True
    for offset in tokens.scan_day_offsets(text):
        if local_now.date().toordinal() + offset != local_start.date().toordinal():
            return True
    for weekday in tokens.scan_weekdays(text):
        if weekday != local_start.weekday():
            return True
    if tokens.has_next_week(text) and (local_start.date() - local_now.date()).days < 7:
        return True
    for item in mentioned_times or ():
        if _structured_time_differs(item, local_start, local_now):
            return True
    return False


# --- khẳng định ngắn sau lượt booking (lỗi LLM-02, dùng cho supervisor) ------
_AFFIRM_YES = ("ừ", "ừa", "ờ", "ừm", "ok", "oke", "okay", "okie", "vâng", "dạ",
               "được", "đc", "dc", "đúng", "uh", "um")
_AFFIRM_YES_BARE = frozenset(strip_diacritics(w) for w in _AFFIRM_YES)
_AFFIRM_FILLER = ("em", "nha", "nhé", "nhe", "ạ", "đi", "rồi", "vậy", "luôn")
_AFFIRM_FILLER_BARE = frozenset(strip_diacritics(w) for w in _AFFIRM_FILLER)
_AFFIRM_MAX_TOKENS = 4


def _is_affirm_yes(token: str) -> bool:
    return token in _AFFIRM_YES or token in _AFFIRM_YES_BARE


def _is_affirm_filler(token: str) -> bool:
    return token in _AFFIRM_FILLER or token in _AFFIRM_FILLER_BARE


def is_short_affirmation(text: str) -> bool:
    """Câu NGẮN, chỉ gồm từ đồng ý + tiểu từ đệm, không nói giờ/ngày khác —
    dùng để định tuyến trong supervisor, không dùng để duyệt lịch."""
    ws = tokens.words(text)
    if not ws or len(ws) > _AFFIRM_MAX_TOKENS:
        return False
    if not any(_is_affirm_yes(t) for t in ws):
        return False
    if not all(_is_affirm_yes(t) or _is_affirm_filler(t) for t in ws):
        return False
    if bool(tokens.scan_times(text)):
        return False
    return True


# --- mô tả đề xuất cho LLM ---------------------------------------------------

def _describe(proposal: Mapping[str, Any]) -> str:
    start = _as_datetime(proposal["start_at"])
    kind = proposal.get("kind", "book")
    text = (f"Proposal: {CLASSIFY_KIND.get(kind, kind)} at "
            f"{format_vi_datetime(start)} ({to_local(start).isoformat()}).")
    if kind in ("move", "cancel"):
        text = f"{text} {CLASSIFY_GO_AHEAD}"
    queued = proposal.get("queued") or []
    if queued:
        when = "; ".join(f"{format_vi_datetime(_as_datetime(q))} "
                         f"({to_local(_as_datetime(q)).isoformat()})" for q in queued)
        text = f"{text} {CLASSIFY_QUEUE.format(when=when)}"
    return text


CLASSIFY_TIMEOUT_SECONDS = 8


class ReplyDecision(BaseModel):
    decision: Literal["approve", "reject", "change", "unclear"]


async def classify_reply(proposal: Mapping[str, Any], bot_question: str,
                         customer_text: str) -> str:
    """Nhãn của model; mọi lỗi/timeout/kết quả lạ -> 'unclear'."""
    try:
        model = build_chat_model(tags=["classify"], streaming=False, reasoning_effort="low")
        structured = model.with_structured_output(ReplyDecision)
        messages = [
            SystemMessage(content=CLASSIFY_PROMPT),
            HumanMessage(content=(
                f"{_describe(proposal)}\n"
                f"Receptionist's last message: {bot_question or '(none)'}\n"
                f"Customer's reply: {customer_text}")),
        ]
        result = await asyncio.wait_for(structured.ainvoke(messages),
                                        timeout=CLASSIFY_TIMEOUT_SECONDS)
        if not isinstance(result, ReplyDecision):
            raise TypeError(f"unexpected classifier result {type(result).__name__}")
        return result.decision
    except Exception as exc:
        logger.warning("classify_failed", extra={"error": str(exc)[:200],
                                                 "error_type": type(exc).__name__})
        return "unclear"


ReplyLabel = Literal["approve", "reject", "change", "unclear"]
REPLY_LABELS = frozenset(ReplyLabel.__args__)
LabelFn = Callable[[Mapping[str, Any], str, str], Awaitable[Optional[str]]]
VetoMode = Literal["full", "time_only"]


async def decide(text: str, proposal: Mapping[str, Any], bot_question: str,
                 now: datetime, *, label_fn: LabelFn, veto_mode: str = "full") -> str:
    """Gọi trực tiếp `label_fn` (chạy `analyzer.analyze_turn`) để lấy quyết định ngữ nghĩa.

    Mọi lỗi/ngoại lệ/kết quả không hợp lệ đều chuyển thành 'unclear' (fail-closed),
    tuyệt đối không suy ra 'approve' khi không chắc chắn.
    """
    try:
        label = await label_fn(proposal, bot_question, text)
    except Exception as exc:
        logger.warning("confirm_label_failed", extra={"error": str(exc)[:200],
                                                      "error_type": type(exc).__name__})
        return "unclear"
    if label not in REPLY_LABELS:
        logger.warning("confirm_label_invalid", extra={"label": str(label)[:40]})
        return "unclear"
    return label


