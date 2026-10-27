"""Đọc câu khách trả lời câu hỏi xác nhận: lưới code phủ quyết trước, model sau.

Thay `is_affirmative` (regex tìm từ "đồng ý" ở BẤT KỲ đâu trong câu — "phải tính
trước cho chắc" từng ghi lịch thật). Hướng đã đảo: code không còn quyết "đồng
ý"; nó chỉ được quyết "chắc chắn KHÔNG phải đồng ý". Câu nào lưới cho qua mới
tới nhãn của model (`label_fn` — bộ phân tích đầu lượt trên đường chính, Task
12b), và chỉ ghi khi model nói approve VÀ lưới không phủ quyết.

Lưới so trên CẢ bản có dấu lẫn bản đã bỏ dấu: khách lớn tuổi gõ không dấu hoặc
nửa dấu nửa không ("dạ khong em"). Bỏ dấu sinh trùng chữ ("Hồng" → "hong",
"đợi" → "doi") nên lưới đôi khi phủ quyết oan — chấp nhận: phủ quyết oan chỉ tốn
một lượt booking hỏi lại, bỏ sót là ghi nhầm lịch.
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

_NEG = ("không", "ko", "k", "hông", "hổng", "hong", "chưa", "thôi", "khỏi", "khoan", "khác")
_NEG_BARE = ("khong", "ko", "k", "hong", "chua", "thoi", "khoi", "khoan", "khac")
_NEG_BY_KIND = {
    "book": ((*_NEG, "đổi", "hủy"), (*_NEG_BARE, "doi", "huy")),
    "move": ((*_NEG, "hủy"), (*_NEG_BARE, "huy")),
    "cancel": ((*_NEG, "đổi"), (*_NEG_BARE, "doi")),
}

_ASKING_ACC = ("hả", "bao lâu", "bao nhiêu", "mấy", "sao", "gì", "đâu", "nào")
_ASKING_BARE = ("ha", "bao lau", "bao nhieu", "sao", "gi", "dau", "nao")
_MAY_UNITS_BARE = frozenset({"gio", "h", "tieng", "phut", "ngay", "nguoi", "lan", "buoi"})
_ASK_ENDINGS = {"không", "hả", "chưa", "sao", "nào"}
_ASK_ENDINGS_BARE = {"khong", "ha", "chua", "sao", "nao"}
_ADDRESS_PARTICLES = {"ạ", "a", "à", "em", "chị", "chi", "anh", "ơi", "oi"}
_SOFT_PARTICLES = {"nha", "nhé", "nhe", "nghen", "đi", "di"}
_PERIOD_FROM_BARE = {"sang": "sáng", "trua": "trưa", "chieu": "chiều", "toi": "tối"}


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _hour_differs(raw_hour: int, period: Optional[str], start_hour: int) -> bool:
    hour = _to_24h(raw_hour, period) if period else None
    if hour is not None:
        return hour != start_hour
    return raw_hour % 12 != start_hour % 12


def _has_negation(text: str, kind: Optional[str]) -> bool:
    neg_acc, neg_bare = _NEG_BY_KIND.get(kind or "book", _NEG_BY_KIND["book"])
    return (tokens.has_phrase(tokens.padded(text), neg_acc)
            or tokens.has_phrase(tokens.padded_unaccented_only(text), neg_bare))


def _has_deferral(text: str) -> bool:
    from app.agents.booking_graph.relatives import defers
    return defers(text)


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


def _is_question(text: str) -> bool:
    if "?" in (text or ""):
        return True
    p_acc = tokens.padded(text).replace(" có gì ", " ")
    p_unacc = tokens.padded_unaccented_only(text).replace(" co gi ", " ")
    if tokens.has_phrase(p_acc, _ASKING_ACC) or tokens.has_phrase(p_unacc, _ASKING_BARE):
        return True
    unacc_list = [t for t in p_unacc.split() if t != "|"]
    for i in range(len(unacc_list) - 1):
        if unacc_list[i] == "may" and unacc_list[i + 1] in _MAY_UNITS_BARE:
            return True
    toks = [t for t in p_acc.split() if t != "|"]
    while toks and toks[-1] in _ADDRESS_PARTICLES:
        toks.pop()
    if toks and toks[-1] in ("vậy", "vay"):
        return True
    while toks and toks[-1] in _SOFT_PARTICLES:
        toks.pop()
    if not toks:
        return False
    last = toks[-1]
    return last in _ASK_ENDINGS or (strip_diacritics(last) == last and last in _ASK_ENDINGS_BARE)


# --- giục làm đúng việc đang chốt (lỗi live B2 #1) -----------------------------
_GO_VERBS = {"move": ({"dời", "đổi"}, {"doi"}), "cancel": ({"hủy", "huỷ"}, {"huy"})}
_GO_AGREE = ({"ừ", "ờ", "ừm", "ok", "oke", "okie", "okay", "dạ", "vâng", "được", "đc"},
             {"u", "o", "um", "ok", "oke", "okie", "okay", "da", "vang", "duoc", "dc"})
_GO_FILLER = ({"đi", "nha", "nhé", "nhá", "nghen", "em", "giùm", "giúp", "dùm", "hộ", "cho",
               "cô", "chú", "chị", "anh", "bác", "ạ", "luôn", "ơi", "lịch", "đó", "cái", "mình"},
              {"di", "nha", "nhe", "nghen", "em", "gium", "giup", "dum", "ho", "cho", "co", "chu",
               "chi", "anh", "bac", "a", "luon", "lun", "oi", "lich", "do", "cai", "minh"})


def _in_set(token: str, sets) -> bool:
    accented, bare = sets
    return token in accented or (strip_diacritics(token) == token and token in bare)


# --- khẳng định ngắn sau lượt booking (lỗi LLM-02) -----------------------------
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
    """Câu NGẮN, chỉ gồm từ đồng ý + tiểu từ đệm, không nói giờ/ngày khác,
    không có chữ phủ định — dùng để định tuyến, không dùng để duyệt lịch."""
    ws = tokens.words(text)
    if not ws or len(ws) > _AFFIRM_MAX_TOKENS:
        return False
    if not any(_is_affirm_yes(t) for t in ws):
        return False
    if not all(_is_affirm_yes(t) or _is_affirm_filler(t) for t in ws):
        return False
    if _has_negation(text, "book") or _has_time(text):
        return False
    return True


def is_go_ahead(text: str, kind: Optional[str]) -> bool:
    """Câu chỉ giục làm đúng việc dời/hủy đang chốt (code quyết, không model)."""
    verbs = _GO_VERBS.get(kind or "")
    if verbs is None:
        return False
    ws = tokens.words(text)
    if not any(_in_set(t, verbs) for t in ws):
        return False
    return all(_in_set(t, verbs) or _in_set(t, _GO_AGREE) or _in_set(t, _GO_FILLER)
               for t in ws)


def other_time_veto(text: str, proposal: Mapping[str, Any], now: datetime) -> bool:
    """Chỉ phần "giờ/ngày/thứ khác" của lưới — TÁCH khỏi phủ định/hoãn/hỏi."""
    lowered = (text or "").strip().lower()
    start = _as_datetime(proposal["start_at"])
    lowered = _without_queued_clauses(lowered, start, proposal.get("queued"), now)
    return mentions_other_time(lowered, start, now)


def veto_reason(text: str, proposal: Mapping[str, Any], now: datetime) -> Optional[str]:
    """Lý do chắc chắn KHÔNG phải đồng ý, hoặc None (lưới không có ý kiến)."""
    lowered = (text or "").strip().lower()
    if _has_negation(lowered, proposal.get("kind")):
        return "negation"
    if _has_deferral(lowered):
        return "deferral"
    start = _as_datetime(proposal["start_at"])
    lowered = _without_queued_clauses(lowered, start, proposal.get("queued"), now)
    if _is_question(lowered):
        return "question"
    if mentions_other_time(lowered, start, now):
        return "other_time"
    return None


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
    if kind in ("move", "cancel"):
        text = f"{text} {CLASSIFY_GO_AHEAD}"
    queued = proposal.get("queued") or []
    if queued:
        # Chỉ GIỜ, không ghi chú: ghi chú là chữ khách gõ (tiếng Việt, có thể
        # tiêm lệnh) — không đưa vào prompt phân loại.
        when = "; ".join(f"{format_vi_datetime(_as_datetime(q))} "
                         f"({to_local(_as_datetime(q)).isoformat()})" for q in queued)
        text = f"{text} {CLASSIFY_QUEUE.format(when=when)}"
    return text


async def classify_reply(proposal: Mapping[str, Any], bot_question: str,
                         customer_text: str) -> str:
    """Nhãn của model; mọi lỗi/timeout/kết quả lạ → "unclear" (không bao giờ
    approve khi model không trả lời rõ). Tag `classify` + `streaming=False`:
    JSON phân loại không bao giờ được stream ra màn hình khách (bẫy #8)."""
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
# Cùng chữ ký với `classify_reply` (proposal, bot_question, customer_text) —
# nên `classify_reply` vẫn cắm vào được để so sánh, dù đường chính (confirm,
# Task 12b) cắm bộ phân tích đầu lượt.
LabelFn = Callable[[Mapping[str, Any], str, str], Awaitable[Optional[str]]]


VetoMode = Literal["full", "time_only"]
# Task 15: ở chế độ "time_only", chỉ lý do "giờ khác" (so với đề xuất) còn bị
# lưới chặn thẳng — code so giờ/ngày chắc chắn hơn model nên vẫn giữ (hỏi độc
# lập qua `other_time_veto`, xem `decide`). Phủ định/hoãn/hỏi để bộ phân tích
# (model) tự đọc, vì chúng dễ bắt oan câu không dấu/nửa dấu hơn.


async def decide(text: str, proposal: Mapping[str, Any], bot_question: str,
                 now: datetime, *, label_fn: LabelFn, veto_mode: VetoMode = "full") -> str:
    """Lưới code TRƯỚC: có lý do phủ quyết thì "reject" và KHÔNG gọi model.
    Rồi go-ahead ("dời đi", "hủy đi" khi đang chốt dời/hủy). Câu lọt qua cả hai
    mới hỏi `label_fn` (bộ phân tích, Task 12b). Chỉ "approve" mới dẫn tới ghi.

    `label_fn` bắt buộc (không có mặc định): người gọi phải chọn rõ nguồn nhãn.
    Nó nổ, hay trả gì ngoài bốn nhãn (kể cả None) → "unclear" — không bao giờ
    suy ra approve từ một câu trả lời không rõ của model.

    `veto_mode` (`CONFIRM_VETO_MODE`, Task 15): "full" (mặc định) giữ nguyên
    bốn lý do phủ quyết; "time_only" chỉ còn lý do "giờ khác", hỏi ĐỘC LẬP qua
    `other_time_veto` (không suy từ `veto_reason`, review controller: lý do
    đầu tiên của `veto_reason` có thể là negation/deferral/question và CHE MẤT
    một câu cũng đang đổi giờ, vd "không, 4 giờ nha"). `is_go_ahead` chỉ được
    hỏi khi lý do ĐẦY ĐỦ (`full_reason`, cả bốn) là None — ở "time_only", một
    câu hỏi/phủ định/hoãn (không đổi giờ) phải đi thẳng tới `label_fn`, không
    qua `is_go_ahead` (bẫy review: "hủy đi nha?" tự động approve mà không gọi
    model nếu `is_go_ahead` chạy trên câu còn mang dấu hỏi).
    """
    full_reason = veto_reason(text, proposal, now)
    reason = ("other_time" if other_time_veto(text, proposal, now) else None
              if veto_mode == "time_only" else full_reason)
    if reason is not None:
        logger.info("confirm_vetoed", extra={"reason": reason})
        return "reject"
    if full_reason is None and is_go_ahead(text, proposal.get("kind")):
        logger.info("confirm_go_ahead", extra={"kind": proposal.get("kind")})
        return "approve"
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
