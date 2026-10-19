"""Đọc câu khách trả lời câu hỏi xác nhận: lưới code phủ quyết trước, model sau.

Thay `is_affirmative` (regex tìm từ "đồng ý" ở BẤT KỲ đâu trong câu — "phải tính
trước cho chắc" từng ghi lịch thật). Hướng đã đảo: code không còn quyết "đồng
ý"; nó chỉ được quyết "chắc chắn KHÔNG phải đồng ý". Câu nào lưới cho qua mới
tới bộ phân loại, và chỉ ghi khi model nói approve VÀ lưới không phủ quyết.

Lưới so trên CẢ bản có dấu lẫn bản đã bỏ dấu: khách lớn tuổi gõ không dấu hoặc
nửa dấu nửa không ("dạ khong em"). Bỏ dấu sinh trùng chữ ("Hồng" → "hong",
"đợi" → "doi") nên lưới đôi khi phủ quyết oan — chấp nhận: phủ quyết oan chỉ tốn
một lượt booking hỏi lại, bỏ sót là ghi nhầm lịch.
"""
import asyncio
import re
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.prompts import (CLASSIFY_GO_AHEAD, CLASSIFY_KIND,
                                              CLASSIFY_PROMPT)
from app.agents.booking_graph.timeparse import (_DAY_OFFSETS, _TIME_PATTERN,
                                                _to_24h)
from app.agents.llm import build_chat_model
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.text import strip_diacritics  # noqa: F401 (re-export)

logger = get_logger(__name__)


def _words(*alternatives: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(alternatives) + r")\b", re.IGNORECASE)


# --- phủ định -----------------------------------------------------------------
# Bộ CÓ DẤU so trên câu gốc. Bộ KHÔNG DẤU chỉ so trên những chữ khách gõ KHÔNG
# dấu (chữ có dấu bị che trước — `_unaccented_only`): bỏ dấu cả câu thì "Hồng"
# thành "hong", "đợi" thành "doi", "thời" thành "thoi", "chùa" thành "chua" —
# phủ quyết oan những câu đồng ý thật (review M3). "dạ khong em" (nửa dấu nửa
# không) vẫn bị bắt vì "khong" được gõ không dấu.
#
# "hủy" để riêng: đang chốt HỦY thì "ừ hủy đi" là ĐỒNG Ý (bẫy #19). Cùng lý do,
# đang chốt DỜI thì "dời"/"đổi" là động từ đồng ý ("ừ dời đi em").
_NEG = ("không", "ko", "k", "hông", "hổng", "hong", "chưa", "thôi", "khỏi", "khoan", "khác")
_NEG_BARE = ("khong", "ko", "k", "hong", "chua", "thoi", "khoi", "khoan", "khac")
_NEG_BY_KIND = {
    "book": (_words(*_NEG, "đổi", "hủy"), _words(*_NEG_BARE, "doi", "huy")),
    "move": (_words(*_NEG, "hủy"), _words(*_NEG_BARE, "huy")),
    "cancel": (_words(*_NEG, "đổi"), _words(*_NEG_BARE, "doi")),
}

# --- hoãn ---------------------------------------------------------------------
# Mẫu NHIỀU chữ: so trên câu đã bỏ dấu toàn bộ — cụm "de chi hoi" khó trùng nhầm.
_DEFERRAL = re.compile(
    r"\bđể\s+\w+(\s+\w+)?\s+(hỏi|xem|gọi|tính|nghĩ|bàn)\b|\bxem lại\b|\bhỏi lại\b"
    r"|\bsuy nghĩ\b|\btính sau\b", re.IGNORECASE)
_DEFERRAL_BARE = re.compile(
    r"\bde\s+\w+(\s+\w+)?\s+(hoi|xem|goi|tinh|nghi|ban)\b|\bxem lai\b|\bhoi lai\b"
    r"|\bsuy nghi\b|\btinh sau\b", re.IGNORECASE)

# --- câu hỏi ------------------------------------------------------------------
# Từ để hỏi ở BẤT KỲ đâu (F5): câu tán gẫu "mất bao lâu hả chị, … cho chắc"
# không có "?" và không kết thúc bằng từ hỏi, nhưng rõ ràng là đang hỏi.
# "có gì" là "nếu có chuyện gì" ("có gì em nhắn chị nha"), không phải câu hỏi.
_ASKING = _words("hả", "bao lâu", "bao nhiêu", "mấy", "sao", "gì", "đâu", "nào")
# "may" không dấu vừa là "mấy" vừa là "may" (may quá) — chỉ tính khi đi kèm đơn vị.
_ASKING_BARE = re.compile(
    r"\b(ha|bao lau|bao nhieu|sao|gi|dau|nao)\b"
    r"|\bmay\s+(gio|h|tieng|phut|ngay|nguoi|lan|buoi)\b", re.IGNORECASE)
_NOT_ASKING = re.compile(r"\bcó gì\b|\bco gi\b", re.IGNORECASE)
_ASK_ENDINGS = {"không", "hả", "chưa", "sao", "nào"}
_ASK_ENDINGS_BARE = {"khong", "ha", "chua", "sao", "nao"}
# Tiểu từ xưng hô cuối câu: "vậy em", "chưa ạ" — bỏ trước khi xem từ cuối.
_ADDRESS_PARTICLES = {"ạ", "a", "à", "em", "chị", "chi", "anh", "ơi", "oi"}
# Tiểu từ rủ rê: "ừ vậy nha", "ok vậy đi" là ĐỒNG Ý, không phải hỏi.
_SOFT_PARTICLES = {"nha", "nhé", "nhe", "nghen", "đi", "di"}


def _unaccented_only(lowered: str) -> str:
    """Giữ những chữ khách gõ không dấu; chữ có dấu thay bằng "_"."""
    return re.sub(r"\w+", lambda m: m.group() if strip_diacritics(m.group()) == m.group()
                  else "_", lowered)


# --- giờ / ngày khác ----------------------------------------------------------
# Dùng lại mẫu của parser (bẫy #11: không sửa, không nới — chỉ đọc). Bản không
# dấu SUY từ chính mẫu đó, không chép tay.
_TIME = re.compile(_TIME_PATTERN, re.IGNORECASE)
_TIME_BARE = re.compile(strip_diacritics(_TIME_PATTERN), re.IGNORECASE)
_PERIOD_FROM_BARE = {strip_diacritics(p): p for p in ("sáng", "trưa", "chiều", "tối")}
# Phút ngay sau giờ: "15h30", "3 giờ rưỡi", "3 giờ 15". Số phút phải HAI chữ số
# để "3 giờ chiều 2 người" không thành 3:02. Cộng hai dạng không cần "giờ":
# "15:30", "3 rưỡi". Không đụng parser (bẫy #11) — chỉ đọc thêm ở đây.
_MINUTE_AFTER = re.compile(r"\s*(rưỡi|ruoi|\d{2})\b", re.IGNORECASE)
_COLON_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_HALF_PAST = re.compile(r"\b(\d{1,2})\s*(rưỡi|ruoi)\b", re.IGNORECASE)

_DAYS = sorted(_DAY_OFFSETS, key=len, reverse=True)
_DAY = _words(*_DAYS)
_DAY_OFFSETS_BARE = {strip_diacritics(k): v for k, v in _DAY_OFFSETS.items()}
_DAY_BARE = _words(*sorted(_DAY_OFFSETS_BARE, key=len, reverse=True))

# Thứ trong tuần — regex RIÊNG ở đây, không thêm vào parser (bẫy #11, F6).
_WEEKDAY_NAMES = {"hai": 0, "ba": 1, "tư": 2, "năm": 3, "sáu": 4, "bảy": 5,
                  "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5}
_WEEKDAY_NAMES_BARE = {strip_diacritics(k): v for k, v in _WEEKDAY_NAMES.items()}
_WEEKDAY = re.compile(r"\bthứ\s+(" + "|".join(_WEEKDAY_NAMES) + r")\b|\b(chủ nhật|cn)\b",
                      re.IGNORECASE)
_WEEKDAY_BARE = re.compile(r"\bthu\s+(" + "|".join(_WEEKDAY_NAMES_BARE) + r")\b"
                           r"|\b(chu nhat|cn)\b", re.IGNORECASE)
_NEXT_WEEK = re.compile(r"\btuần\s+(sau|tới)\b|\btuan\s+(sau|toi)\b", re.IGNORECASE)


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _hour_differs(raw_hour: int, period: Optional[str], start_hour: int) -> bool:
    hour = _to_24h(raw_hour, period) if period else None
    if hour is not None:
        return hour != start_hour
    return raw_hour % 12 != start_hour % 12


def _minute_of(token: str) -> int:
    return 30 if token.lower() in ("rưỡi", "ruoi") else int(token)


def _times(text: str, pattern: re.Pattern, periods=None):
    """(giờ, buổi, phút|None) cho mọi mốc giờ nhắc trong câu."""
    for m in pattern.finditer(text):
        period = m.group(2)
        if periods is not None and period:
            period = periods.get(period)
        after = _MINUTE_AFTER.match(text, m.end())
        yield int(m.group(1)), period, (_minute_of(after.group(1)) if after else None)
    for m in _COLON_TIME.finditer(text):
        yield int(m.group(1)), None, int(m.group(2))
    for m in _HALF_PAST.finditer(text):
        yield int(m.group(1)), None, 30


def mentions_other_time(text: str, start: datetime, now: datetime) -> bool:
    """Khách nhắc một giờ, một ngày hay một thứ KHÁC với đề xuất.

    "ừ mà 4 giờ được không", "ok nhưng thứ năm nha", "dạ 4 giờ nha em" — có chữ
    đồng ý nhưng đang đổi giờ. Giờ không có buổi so theo `hour % 12` ("3 giờ" và
    15:00 là một); có buổi thì so đủ 24 giờ ("3 giờ sáng" khác 15:00). Có phút
    thì so cả phút ("15h30", "3 rưỡi" khác 15:00).
    """
    lowered = (text or "").lower()
    bare = strip_diacritics(lowered)
    typed_bare = bare == lowered            # khách gõ hoàn toàn không dấu
    local_start, local_now = to_local(start), to_local(now)

    for hour, period, minute in [*_times(lowered, _TIME),
                                 *_times(bare, _TIME_BARE, _PERIOD_FROM_BARE)]:
        if _hour_differs(hour, period, local_start.hour):
            return True
        if minute is not None and minute != local_start.minute:
            return True

    def day_differs(offset: int) -> bool:
        return local_now.date().toordinal() + offset != local_start.date().toordinal()

    for m in _DAY.finditer(lowered):
        if day_differs(_DAY_OFFSETS[m.group(1).lower()]):
            return True
    for m in _DAY_BARE.finditer(bare):
        word = m.group(1).lower()
        # "mot" không dấu vừa là "mốt" vừa là "một" — chỉ tin khi khách gõ
        # hẳn không dấu; có dấu thì "mốt" đã được bắt ở bản có dấu.
        if word == "mot" and not typed_bare:
            continue
        if day_differs(_DAY_OFFSETS_BARE[word]):
            return True

    def weekday_of(m, names) -> int:
        return names[m.group(1).lower()] if m.group(1) else 6

    for m in _WEEKDAY.finditer(lowered):
        if weekday_of(m, _WEEKDAY_NAMES) != local_start.weekday():
            return True
    for m in _WEEKDAY_BARE.finditer(bare):
        if weekday_of(m, _WEEKDAY_NAMES_BARE) != local_start.weekday():
            return True

    if _NEXT_WEEK.search(lowered) or _NEXT_WEEK.search(bare):
        if (local_start.date() - local_now.date()).days < 7:
            return True
    return False


def _is_question(lowered: str, unaccented: str) -> bool:
    lowered = _NOT_ASKING.sub(" ", lowered)
    unaccented = _NOT_ASKING.sub(" ", unaccented)
    if "?" in lowered or _ASKING.search(lowered) or _ASKING_BARE.search(unaccented):
        return True
    tokens = re.findall(r"\w+", lowered)
    while tokens and tokens[-1] in _ADDRESS_PARTICLES:
        tokens.pop()
    # "vậy" cuối câu là hỏi ("thế à vậy"), nhưng "ừ vậy nha/đi" là đồng ý: chỉ
    # tính khi KHÔNG có tiểu từ rủ rê đứng sau.
    if tokens and tokens[-1] in ("vậy", "vay"):
        return True
    while tokens and tokens[-1] in _SOFT_PARTICLES:
        tokens.pop()
    if not tokens:
        return False
    last = tokens[-1]
    return last in _ASK_ENDINGS or (strip_diacritics(last) == last and last in _ASK_ENDINGS_BARE)


def veto_reason(text: str, proposal: Mapping[str, Any], now: datetime) -> Optional[str]:
    """Lý do chắc chắn KHÔNG phải đồng ý, hoặc None (lưới không có ý kiến).

    "negation" | "deferral" | "question" | "other_time". None KHÔNG có nghĩa là
    đồng ý — chỉ là để bộ phân loại quyết.
    """
    lowered = (text or "").strip().lower()
    bare = strip_diacritics(lowered)
    unaccented = _unaccented_only(lowered)

    negation, negation_bare = _NEG_BY_KIND.get(proposal.get("kind"), _NEG_BY_KIND["book"])
    if negation.search(lowered) or negation_bare.search(unaccented):
        return "negation"
    if _DEFERRAL.search(lowered) or _DEFERRAL_BARE.search(bare):
        return "deferral"
    if _is_question(lowered, unaccented):
        return "question"
    if mentions_other_time(lowered, _as_datetime(proposal["start_at"]), now):
        return "other_time"
    return None


# --- bộ phân loại -------------------------------------------------------------

# Như timeout parser (bẫy #10): Azure thật mất 2–3 giây; ngưỡng ngắn hơn là
# nhánh model không bao giờ chạy và mọi câu thành `unclear`.
CLASSIFY_TIMEOUT_SECONDS = 8


class ReplyDecision(BaseModel):
    decision: Literal["approve", "reject", "change", "unclear"]


def _describe(proposal: Mapping[str, Any]) -> str:
    start = _as_datetime(proposal["start_at"])
    kind = proposal.get("kind", "book")
    text = (f"Proposal: {CLASSIFY_KIND.get(kind, kind)} at "
            f"{format_vi_datetime(start)} ({to_local(start).isoformat()}).")
    # Dời/hủy có động từ riêng mà khách hay dùng để giục ("dời đi", "hủy đi");
    # đặt mới thì không — giữ nguyên mô tả cũ.
    return f"{text} {CLASSIFY_GO_AHEAD}" if kind in ("move", "cancel") else text


async def classify_reply(proposal: Mapping[str, Any], bot_question: str,
                         customer_text: str) -> str:
    """Nhãn của model; mọi lỗi/timeout/kết quả lạ → "unclear" (không bao giờ
    approve khi model không trả lời rõ). Tag `classify` + `streaming=False`:
    JSON phân loại không bao giờ được stream ra màn hình khách (bẫy #8)."""
    try:
        model = build_chat_model(tags=["classify"], temperature=0.0, streaming=False)
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


async def decide(text: str, proposal: Mapping[str, Any], bot_question: str,
                 now: datetime) -> str:
    """Lưới code TRƯỚC: có lý do phủ quyết thì "reject" và KHÔNG gọi model.
    Không thì nhãn của bộ phân loại. Chỉ "approve" mới dẫn tới ghi lịch."""
    reason = veto_reason(text, proposal, now)
    if reason is not None:
        logger.info("confirm_vetoed", extra={"reason": reason})
        return "reject"
    return await classify_reply(proposal, bot_question, text)
