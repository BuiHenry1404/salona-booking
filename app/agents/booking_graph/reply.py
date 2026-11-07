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
from app.agents.booking_graph.timeparse import _to_24h  # noqa: F401 (re-export)
# Lưới so giờ dời sang gói mới (Task 6.1); tên `_riêng` liệt kê tường minh vì
# `import *` bỏ qua chúng mà code/test cũ vẫn dùng.
from app.agents.booking.timecheck import *  # noqa: F401,F403
from app.agents.booking.timecheck import (  # noqa: F401
    _PERIOD_FROM_BARE, _as_datetime, _has_time, _hour_differs, _structured_time_differs,
    _without_queued_clauses, mentions_other_time, other_time, restates_proposal_time,
    spoken_times)
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.text import strip_diacritics  # noqa: F401 (re-export)

logger = get_logger(__name__)

def _time_matches(hour: int, period: Optional[str], minute: Optional[int],
                  local: datetime) -> bool:
    return (not _hour_differs(hour, period, local.hour)
            and (minute is None or minute == local.minute))


def names_only_queued_times(text: str, proposal: Mapping[str, Any], now: datetime) -> bool:
    """Mọi giờ khách nói đều trùng một lịch ĐANG CHỜ và không giờ nào trùng đề
    xuất ("ừ, 2 giờ chiều" khi đề xuất 9 giờ, chờ 14 giờ) — "ừ" đó không dành
    cho đề xuất này."""
    times = tokens.scan_times((text or "").strip().lower())
    waiting = [to_local(_as_datetime(q)) for q in (proposal.get("queued") or [])]
    if not times or not waiting:
        return False
    local_start = to_local(_as_datetime(proposal["start_at"]))
    return (all(any(_time_matches(*t, q) for q in waiting) for t in times)
            and not any(_time_matches(*t, local_start) for t in times))


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
    4. model nói "change" nhưng khách chỉ nhắc lại đúng giờ đề xuất, không nêu
       dịch vụ → "unclear" (không bao giờ đổi thành approve; có `service` thì
       vẫn là đổi);
    5. model nói "approve" nhưng khách chỉ nêu giờ của lịch đang chờ →
       "unclear";
    6. còn lại: nhãn model nếu hợp lệ, không thì "unclear".
    """
    aside = analysis.get("aside")
    label = analysis.get("reply_to_proposal")
    if aside in ("defer_only", "defer_with_request"):
        return "reject"
    if aside == "relative_free" and label == "approve":
        return "unclear"
    if other_time(text, proposal, now, analysis.get("mentioned_times") or ()):
        return "change"
    if (label == "change" and not analysis.get("service")
            and restates_proposal_time(text, proposal, now)):
        return "unclear"
    if label == "approve" and names_only_queued_times(text, proposal, now):
        return "unclear"
    return label if label in REPLY_LABELS else "unclear"
