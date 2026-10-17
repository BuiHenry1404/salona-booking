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
import unicodedata
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.prompts import CLASSIFY_KIND, CLASSIFY_PROMPT
from app.agents.booking_graph.timeparse import (_DAY_OFFSETS, _TIME_PATTERN,
                                                _to_24h)
from app.agents.llm import build_chat_model
from app.core.clock import to_local
from app.core.logging import get_logger

logger = get_logger(__name__)


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt. 'đúng rồi' và 'dung roi' phải cùng ra một chuỗi.

    NFD tách dấu thành ký tự riêng để lọc, nhưng đ/Đ không phải d kèm dấu — nó
    là một ký tự độc lập nên phải thay tay.
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _words(*alternatives: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(alternatives) + r")\b", re.IGNORECASE)


# --- phủ định -----------------------------------------------------------------
# "hủy" để riêng: đang chốt HỦY thì "ừ hủy đi" là ĐỒNG Ý (bẫy #19).
_NEG = ("không", "ko", "k", "hông", "hổng", "hong", "chưa", "thôi", "khỏi", "khoan",
        "đổi", "khác")
_NEG_BARE = ("khong", "ko", "k", "hong", "chua", "thoi", "khoi", "khoan", "doi", "khac")
_NEGATION = _words(*_NEG, "hủy")
_NEGATION_BARE = _words(*_NEG_BARE, "huy")
_NEGATION_CANCELLING = _words(*_NEG)
_NEGATION_CANCELLING_BARE = _words(*_NEG_BARE)

# --- hoãn ---------------------------------------------------------------------
_DEFERRAL = re.compile(
    r"\bđể\s+\w+(\s+\w+)?\s+(hỏi|xem|gọi|tính|nghĩ|bàn)\b|\bxem lại\b|\bhỏi lại\b"
    r"|\bsuy nghĩ\b|\btính sau\b", re.IGNORECASE)
_DEFERRAL_BARE = re.compile(
    r"\bde\s+\w+(\s+\w+)?\s+(hoi|xem|goi|tinh|nghi|ban)\b|\bxem lai\b|\bhoi lai\b"
    r"|\bsuy nghi\b|\btinh sau\b", re.IGNORECASE)

# --- câu hỏi ------------------------------------------------------------------
# Từ để hỏi ở BẤT KỲ đâu (F5): câu tán gẫu "mất bao lâu hả chị, … cho chắc"
# không có "?" và không kết thúc bằng từ hỏi, nhưng rõ ràng là đang hỏi.
_ASKING = _words("hả", "bao lâu", "bao nhiêu", "mấy", "sao", "gì", "đâu", "nào")
_ASKING_BARE = _words("ha", "bao lau", "bao nhieu", "may", "sao", "gi", "dau", "nao")
_ASK_ENDINGS = {"không", "hả", "chưa", "sao", "nào", "vậy",
                "khong", "ha", "chua", "nao", "vay"}
# Tiểu từ lịch sự cuối câu, bỏ trước khi xem từ cuối: "vậy em", "chưa ạ".
_TRAILING_PARTICLES = {"ạ", "a", "à", "em", "chị", "chi", "anh", "nha", "nhé", "nhe",
                       "ơi", "oi"}

# --- giờ / ngày khác ----------------------------------------------------------
# Dùng lại mẫu của parser (bẫy #11: không sửa, không nới — chỉ đọc). Bản không
# dấu SUY từ chính mẫu đó, không chép tay.
_TIME = re.compile(_TIME_PATTERN, re.IGNORECASE)
_TIME_BARE = re.compile(strip_diacritics(_TIME_PATTERN), re.IGNORECASE)
_PERIOD_FROM_BARE = {strip_diacritics(p): p for p in ("sáng", "trưa", "chiều", "tối")}

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


def mentions_other_time(text: str, start: datetime, now: datetime) -> bool:
    """Khách nhắc một giờ, một ngày hay một thứ KHÁC với đề xuất.

    "ừ mà 4 giờ được không", "ok nhưng thứ năm nha", "dạ 4 giờ nha em" — có chữ
    đồng ý nhưng đang đổi giờ. Giờ không có buổi so theo `hour % 12` ("3 giờ" và
    15:00 là một); có buổi thì so đủ 24 giờ ("3 giờ sáng" khác 15:00).
    """
    lowered = (text or "").lower()
    bare = strip_diacritics(lowered)
    typed_bare = bare == lowered            # khách gõ hoàn toàn không dấu
    local_start, local_now = to_local(start), to_local(now)

    for m in _TIME.finditer(lowered):
        if _hour_differs(int(m.group(1)), m.group(2), local_start.hour):
            return True
    for m in _TIME_BARE.finditer(bare):
        period = _PERIOD_FROM_BARE.get(m.group(2)) if m.group(2) else None
        if _hour_differs(int(m.group(1)), period, local_start.hour):
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


def _is_question(lowered: str, bare: str) -> bool:
    if "?" in lowered or _ASKING.search(lowered) or _ASKING_BARE.search(bare):
        return True
    tokens = re.findall(r"\w+", lowered)
    while tokens and tokens[-1] in _TRAILING_PARTICLES:
        tokens.pop()
    return bool(tokens) and (tokens[-1] in _ASK_ENDINGS
                             or strip_diacritics(tokens[-1]) in _ASK_ENDINGS)


def veto_reason(text: str, proposal: Mapping[str, Any], now: datetime) -> Optional[str]:
    """Lý do chắc chắn KHÔNG phải đồng ý, hoặc None (lưới không có ý kiến).

    "negation" | "deferral" | "question" | "other_time". None KHÔNG có nghĩa là
    đồng ý — chỉ là để bộ phân loại quyết.
    """
    lowered = (text or "").strip().lower()
    bare = strip_diacritics(lowered)
    cancelling = proposal.get("kind") == "cancel"

    negation = _NEGATION_CANCELLING if cancelling else _NEGATION
    negation_bare = _NEGATION_CANCELLING_BARE if cancelling else _NEGATION_BARE
    if negation.search(lowered) or negation_bare.search(bare):
        return "negation"
    if _DEFERRAL.search(lowered) or _DEFERRAL_BARE.search(bare):
        return "deferral"
    if _is_question(lowered, bare):
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
    return (f"Proposal: {CLASSIFY_KIND.get(kind, kind)} at "
            f"{format_vi_datetime(start)} ({to_local(start).isoformat()}).")


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
