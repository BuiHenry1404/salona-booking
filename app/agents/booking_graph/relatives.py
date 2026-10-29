"""Khách nhắc NGƯỜI NHÀ / người quen hoặc hoãn lại lịch.

Nhận diện thuần qua `TurnAnalysis.aside` (Structured Output LLM):
- "relative_free": Khách vừa hỏi người nhà/người quen của khách có rảnh không.
- "defer_only": Khách nói sẽ hỏi lại/tính lại rồi nhắn em sau (chỉ hoãn, không có yêu cầu khác).
- "defer_with_request": Khách hoãn nhưng kèm yêu cầu đặt/hỏi khác.
"""
from typing import Any, Mapping, Optional, Sequence

from app.agents.booking_graph import guard

RELATIVE_LINE = ("Khách vừa hỏi NGƯỜI NHÀ/NGƯỜI QUEN của khách có rảnh không — không phải "
                 "hỏi chủ tiệm bận hay rảnh, không phải hỏi lịch của khách khác. [relative]")
DEFERRAL_LINE = ("Khách nói sẽ hỏi lại/tính lại rồi nhắn em sau — đây là chuyện lịch của "
                 "chính khách; chỉ đáp một câu ngắn, mời khách nhắn lại. [deferral]")

_DEFERRAL_REPLIES = (
    "Dạ vâng, {address} cứ thong thả, xong thì nhắn em nhé.",
    "Dạ, khi nào {address} sẵn sàng thì nhắn lại em là được ạ.",
    "Dạ không sao ạ, có gì {address} cứ nhắn em nhé.",
)


def aside_line(text: str, analysis: Optional[Mapping[str, Any]] = None) -> str:
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    lines = []
    if aside == "relative_free":
        lines.append(RELATIVE_LINE)
    elif aside in ("defer_only", "defer_with_request"):
        lines.append(DEFERRAL_LINE)
    return " ".join(lines)


def deferral_reply(text: str, address: str, previous_replies: Sequence[str],
                   analysis: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    if aside != "defer_only":
        return None
    options = [r.format(address=address) for r in _DEFERRAL_REPLIES]
    previous = [p for p in previous_replies or [] if p]
    return next((o for o in options if o not in previous and not guard.repeats(o, previous)), options[-1])


