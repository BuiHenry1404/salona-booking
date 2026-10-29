"""Đọc câu khách trả lời câu hỏi xác nhận: lưới code giờ/ngày TRƯỚC, rồi `TurnAnalysis`.

Toàn bộ các bộ từ điển từ khóa chuỗi tĩnh (_NEG_PHRASES_*, _GO_VERBS, veto_reason,
và bộ phân loại `classify_reply` gọi model riêng cho bước này) đã được loại bỏ.
Code chỉ còn so giờ/ngày/thứ (`other_time`, quét bằng `tokens.scan_*`); phủ
định/hoãn/hỏi giao cho bộ phân tích ngữ nghĩa đầu lượt (`label_fn` — tức
`analyzer.analyze_turn` — + `resolve_label`), với cơ chế fail-closed an toàn
(lỗi/không rõ -> 'unclear'). `_describe` vẫn ở đây vì `analyzer.py` dùng nó để
mô tả đề xuất cho bộ phân tích đó.
"""
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal, Mapping, Optional, Sequence

from app.agents.booking_graph import tokens
from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.prompts import CLASSIFY_GO_AHEAD, CLASSIFY_KIND, CLASSIFY_QUEUE
from app.agents.booking_graph.timeparse import _to_24h
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


def _has_time(text: str) -> bool:
    return bool(tokens.scan_times(text))


def _without_queued_clauses(lowered: str, start: datetime, queued, now: datetime) -> str:
    """Bỏ những mệnh đề chỉ nói về một lịch ĐANG CHỜ (lỗi live B2 #2)."""
    if not queued:
        return lowered
    waiting = [_as_datetime(q) for q in queued]
    kept = []
    for clause in tokens.split_clauses(lowered, break_words=("còn", "con"), preserve_date_comma=False):
        if (clause.strip() and _has_time(clause)
                and mentions_other_time(clause, start, now)
                and any(not mentions_other_time(clause, q, now) for q in waiting)):
            continue
        kept.append(clause)
    remainder = " , ".join(c.strip() for c in kept if c.strip())
    return remainder if remainder else lowered


def other_time(text: str, proposal: Mapping[str, Any], now: datetime,
               mentioned_times: Sequence[Any] = ()) -> bool:
    """Khách nói một giờ/ngày/thứ KHÁC đề xuất — lưới code, chắc hơn nhãn model.

    Quét chữ sau khi bỏ mệnh đề chỉ nói về lịch đang chờ. Với `mentioned_times`
    (đối tượng `MentionedTime` hay dict từ `model_dump()`): bỏ mục khớp một giờ
    đang chờ, phần còn lại so với `start_at`.
    """
    start = _as_datetime(proposal["start_at"])
    queued = [_as_datetime(q) for q in (proposal.get("queued") or [])]
    lowered = _without_queued_clauses((text or "").strip().lower(), start, queued, now)
    if mentions_other_time(lowered, start, now):
        return True
    local_start, local_now = to_local(start), to_local(now)
    local_queued = [to_local(q) for q in queued]
    for item in mentioned_times or ():
        if any(not _structured_time_differs(item, local_q, local_now) for local_q in local_queued):
            continue
        if _structured_time_differs(item, local_start, local_now):
            return True
    return False


def restates_proposal_time(text: str, proposal: Mapping[str, Any], now: datetime) -> bool:
    """Khách CÓ nói giờ và mọi giờ đều trùng đề xuất (chỉ dựa vào quét chữ)."""
    return _has_time(text) and not other_time(text, proposal, now)


def spoken_times(text: str) -> Optional[str]:
    """Các giờ khách nói, viết lại bằng CODE ("4 giờ", "10 giờ 30 sáng").

    Không bao giờ chép chữ khách (chống tiêm lệnh). Không có giờ → None.
    """
    parts = []
    for hour, period, minute in tokens.scan_times(text):
        spoken = f"{hour} giờ"
        if minute:  # minute=0 bị bỏ qua cố ý: "3 giờ" đọc tự nhiên hơn "3 giờ 0"
            spoken = f"{spoken} {minute}"
        if period:
            spoken = f"{spoken} {period}"
        parts.append(spoken)
    return ", ".join(parts) if parts else None


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


ReplyLabel = Literal["approve", "reject", "change", "unclear"]
REPLY_LABELS = frozenset(ReplyLabel.__args__)
LabelFn = Callable[[Mapping[str, Any], str, str], Awaitable[Optional[str]]]


async def decide(text: str, proposal: Mapping[str, Any], bot_question: str,
                 now: datetime, *, label_fn: LabelFn) -> str:
    """Lưới code TRƯỚC: khách nói giờ/ngày/thứ khác đề xuất → "change", KHÔNG
    gọi model (model hay gắn approve cho "ok mà 4 giờ nha"). Câu lọt qua mới
    hỏi `label_fn` (bộ phân tích + `resolve_label`).

    Mọi lỗi/ngoại lệ/kết quả không hợp lệ đều chuyển thành 'unclear' (fail-closed),
    tuyệt đối không suy ra 'approve' khi không chắc chắn.
    """
    if other_time(text, proposal, now):
        logger.info("confirm_vetoed", extra={"reason": "other_time"})
        return "change"
    try:
        label = await label_fn(proposal, bot_question, text)
    except Exception as exc:
        # CancelledError (lượt mới thay) là BaseException — không bị nuốt.
        logger.warning("confirm_label_failed", extra={"error": str(exc)[:200],
                                                      "error_type": type(exc).__name__})
        return "unclear"
    if label not in REPLY_LABELS:
        logger.warning("confirm_label_invalid", extra={"label": str(label)[:40]})
        return "unclear"
    return label


def resolve_label(analysis: Mapping[str, Any], text: str, proposal: Mapping[str, Any],
                  now: datetime) -> str:
    """Nhãn cuối từ `TurnAnalysis` (dạng dict) — hàm thuần, code đè nhãn model:

    1. hoãn (`defer_only`/`defer_with_request`) → "reject";
    2. đồng ý mà còn hỏi người nhà rảnh không (`relative_free`) → "unclear";
    3. giờ có cấu trúc khác đề xuất → "change";
    4. model nói "change" nhưng khách chỉ nhắc lại đúng giờ đề xuất → "unclear"
       (không bao giờ đổi thành approve);
    5. còn lại: nhãn model nếu hợp lệ, không thì "unclear".
    """
    aside = analysis.get("aside")
    label = analysis.get("reply_to_proposal")
    if aside in ("defer_only", "defer_with_request"):
        return "reject"
    if aside == "relative_free" and label == "approve":
        return "unclear"
    if other_time(text, proposal, now, analysis.get("mentioned_times") or ()):
        return "change"
    if label == "change" and restates_proposal_time(text, proposal, now):
        return "unclear"
    return label if label in REPLY_LABELS else "unclear"
