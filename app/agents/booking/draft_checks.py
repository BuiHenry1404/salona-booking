"""Các phép kiểm TẤT ĐỊNH trên câu nháp (không gọi LLM) — dời nguyên văn từ
`guard.py` của gói cũ ở Task 6.1: xoá tên tool (`strip_tool_names`), gỡ lời
nhận vơ (`unclaim`, `has_false_claim`, `false_claim`), đọc mốc giờ trong câu
(`clock_keys`) và bắt lặp câu (`repeats`).

Tách thành module riêng thay vì gộp thẳng vào `guard.py` vì hai lẽ:
- `rewrite.py` cần `REPEAT_LOOKBACK`/`REWRITE_TIMEOUT_SECONDS`, mà `guard.py`
  lại import `rewrite` — để chung là vòng import;
- `normalize` ở đây (nối lại các từ của `tokens.words`, phục vụ `repeats`) khác hẳn
  `guard.normalize` (NFC + casefold, phục vụ so `must_say`) — chung một module
  thì phải đổi tên một trong hai.

Hai regex DUY NHẤT của gói `app/agents/booking/` nằm ở đây (`_TOOL_GROUP`,
`_TOOL_BARE`) — `tests/test_regex_budget.py` ghim đúng con số đó.
"""
from difflib import SequenceMatcher
import re
from typing import List, Optional, Sequence, Tuple

from app.agents.booking import tokens

REPEAT_RATIO = 0.85          # điểm khởi đầu — khoá sau khi duyệt scripts/probe_repeats.py
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 8         # đo trên transcript chạy thật 2026-09-19: 6 đã bắt hết,
                            # 8 lấy biên. Mốc cũ 3 mù với câu từ chối tái xuất
                            # sau vài lượt — LLM-32 lặp ở lượt 1, 2, 6, 14, 16.
REWRITE_TIMEOUT_SECONDS = 8

TOOL_NAMES = ("parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
              "cancel_appointment", "get_shop_hours", "get_shop_status", "is_open_at")
_TOOL_ALT = "|".join(TOOL_NAMES)
_SNAKE_IDENT = r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"
_TOOL_GROUP = re.compile(
    rf"[ \t]*[(\[][^()\[\]]*?\b(?:{_TOOL_ALT}|{_SNAKE_IDENT})\b[^()\[\]]*[)\]]")
_BARE_NAME = rf"`?\b(?:{_TOOL_ALT})\b(?:\(\))?`?"
_TOOL_BARE = re.compile(rf"{_BARE_NAME}(?:[ \t]*(?:,|và|and)[ \t]*{_BARE_NAME})*")


def _tidy_line(line: str) -> str:
    collapsed = " ".join(line.split())
    out: List[str] = []
    for ch in collapsed:
        if ch in ".,!?…;:" and out and out[-1] == " ":
            out.pop()
        if ch == "," and out and out[-1] == ",":
            continue
        out.append(ch)
    return "".join(out).strip()


def strip_tool_names(text: str) -> str:
    """Bỏ tên tool khỏi câu sắp gửi khách: nhóm ngoặc chứa tên tool/định danh
    snake_case, tên trần; rồi gộp khoảng trắng và bỏ khoảng trắng trước dấu câu.

    Không có gì để bỏ thì trả NGUYÊN chuỗi (kể cả xuống dòng) — câu sạch không
    bị đụng tới."""
    if not text:
        return text
    cleaned = _TOOL_BARE.sub("", _TOOL_GROUP.sub("", text))
    if cleaned == text:
        return text
    return "\n".join(_tidy_line(line) for line in cleaned.split("\n")).strip()


_UNCLAIM_LEADS = (
    ("Dạ, em đã ", "Dạ, em "),
    ("Dạ, em vừa ", "Dạ, em "),
    ("Dạ em đã ", "Dạ em "),
    ("Dạ em vừa ", "Dạ em "),
    ("dạ, em đã ", "dạ, em "),
    ("dạ, em vừa ", "dạ, em "),
    ("Dạ, đã ", "Dạ, em "),
    ("Dạ, vừa ", "Dạ, em "),
    ("Dạ đã ", "Dạ em "),
    ("Dạ vừa ", "Dạ em "),
    ("dạ, đã ", "dạ, em "),
    ("Em đã ", "Em "),
    ("Em vừa ", "Em "),
    ("em đã ", "em "),
    ("em vừa ", "em "),
    ("Đã ", "Em "),
    ("Vừa ", "Em "),
)
_MID_LEADS = (
    ("Em đã ", "Em "),
    ("Em vừa ", "Em "),
    ("em đã ", "em "),
    ("em vừa ", "em "),
)
_CLAIM_VERBS = ("đặt", "chốt", "ghi", "dời", "đổi", "hủy", "huỷ")
_CLAIM_OBJS = (" lịch", " chỗ", " hẹn")


def _strip_first_roi(seg: str) -> str:
    for suffix in (" rồi ạ", " rồi,", " rồi.", " rồi!", " rồi?", " rồi…"):
        idx = seg.find(suffix)
        if idx != -1:
            return seg[:idx] + seg[idx + 4:]
    if seg.endswith(" rồi"):
        return seg[:-4]
    return seg


def _unclaim_sentence(sent: str) -> str:
    candidates: List[Tuple[int, str, str]] = []
    for raw_lead, repl_lead in _UNCLAIM_LEADS:
        if sent.startswith(raw_lead):
            candidates.append((0, raw_lead, repl_lead))
            break
    if not candidates:
        for raw_lead, repl_lead in _MID_LEADS:
            pos = sent.find(raw_lead)
            if pos > 0 and not sent[pos - 1].isalnum():
                candidates.append((pos, raw_lead, repl_lead))
                break
    if not candidates:
        return sent
    pos, raw_lead, repl_lead = candidates[0]
    rest = sent[pos + len(raw_lead):]
    matched_verb = None
    for v in _CLAIM_VERBS:
        if rest.startswith(v) and (len(rest) == len(v) or not rest[len(v)].isalnum()):
            matched_verb = v
            break
    if not matched_verb:
        return sent
    after_verb = rest[len(matched_verb):]
    if matched_verb == "ghi" and (after_verb.startswith(" nhận") or after_verb.startswith(" lại")):
        return sent
    obj = ""
    for o in _CLAIM_OBJS:
        if after_verb.startswith(o) and (len(after_verb) == len(o) or not after_verb[len(o)].isalnum()):
            obj = o
            after_verb = after_verb[len(o):]
            break
    if after_verb.startswith(" xong") and (len(after_verb) == 5 or not after_verb[5].isalnum()):
        after_verb = after_verb[5:]
    if matched_verb in ("đặt", "chốt", "ghi"):
        return f"{sent[:pos]}{repl_lead}giữ chỗ{after_verb}"
    action = "dời" if matched_verb in ("dời", "đổi") else "hủy"
    cleaned_tail = _strip_first_roi(after_verb)
    return f"{sent[:pos]}{repl_lead}sẽ {action}{obj}{cleaned_tail}"


def unclaim(draft: str) -> str:
    """"em đã đặt" → "em giữ chỗ"; "em đã dời/hủy … rồi" → "em sẽ dời/hủy …"."""
    sents = tokens.split_sentences(draft)
    if not sents:
        return draft
    rewritten = [_unclaim_sentence(s) for s in sents]
    if rewritten == sents:
        return draft
    return " ".join(rewritten)


def clock_keys(text: str) -> List[tuple]:
    """(giờ đọc, phút, buổi|None) cho mọi giờ đọc kiểu "9 giờ sáng" trong câu."""
    return tokens.scan_spoken_clocks(text)


def _same_clock(said: tuple, known: tuple) -> bool:
    return said[:2] == known[:2] and (said[2] is None or said[2] == known[2])


_CLAIM_PHRASES = (
    "đã giữ", "đã đặt", "đã ghi", "đã chốt",
    "vừa giữ", "vừa đặt", "vừa ghi", "vừa chốt",
    "đang giữ", "đang đặt", "đang ghi", "đang chốt",
    "đã được giữ", "đã được đặt", "đã được ghi", "đã được chốt",
    "vừa được giữ", "vừa được đặt",
    "đang được giữ", "đang được đặt",
)
_CLAIM_HOLD_VERBS = frozenset({"giữ", "đặt", "ghi", "chốt"})


def _is_claimed_clause(clause: str) -> bool:
    p = tokens.padded(clause)
    if tokens.has_phrase(p, ("chưa", "không", "ko")):
        return False
    p_clean = p.replace(" ghi nhận ", " ").replace(" ghi lại ", " ")
    if tokens.has_phrase(p_clean, _CLAIM_PHRASES):
        return True
    ws = [t for t in p_clean.split() if t != "|"]
    for i, w in enumerate(ws):
        if w in _CLAIM_HOLD_VERBS and "rồi" in ws[i + 1:]:
            return True
    return False


def _clauses(sentence: str) -> List[str]:
    return tokens.split_clauses(sentence, break_words=("còn",), preserve_date_comma=True)


def false_claim(clause: str, facts: dict) -> bool:
    if facts.get("unknown") or not _is_claimed_clause(clause):
        return False
    said = clock_keys(clause)
    if not said:
        return not facts["any"]
    return any(not any(_same_clock(s, k) for k in facts["clocks"]) for s in said)


def has_false_claim(text: str, facts: Optional[dict]) -> bool:
    if not facts:
        return False
    return any(false_claim(c, facts) for s in tokens.split_sentences(text or "") for c in _clauses(s))


def normalize(text: str) -> str:
    return " ".join(tokens.words(text))


def sentences(text: str) -> List[str]:
    return [p.rstrip(".!?…").strip() for p in tokens.split_sentences(text)
            if p.rstrip(".!?…").strip()]


def _digits(text: str) -> set:
    return set(tokens.digits_in(text))


def repeats(draft: str, previous_replies: Sequence[str],
            ratio: float = REPEAT_RATIO, min_words: int = REPEAT_MIN_WORDS) -> bool:
    """Cấp 2 (độ giống cả câu trả lời) + cấp 3 (từng câu ≥ min_words từ), so với
    REPEAT_LOOKBACK câu đáp LLM gần nhất. Không so nguyên văn: ca thật chỉ khác
    "của chị" → "của chị Thắm".

    Số liệu (giờ, ngày, ...) khác nhau → không phải lặp, mà là thông tin mới
    dùng lại cùng khung câu (VD đổi giờ giữ chỗ) — bỏ qua phép so đó."""
    d = normalize(draft)
    d_digits = _digits(draft)
    d_sents = [normalize(s) for s in sentences(draft)]
    for prev in list(previous_replies)[-REPEAT_LOOKBACK:]:
        # Số liệu khác nhau → khung câu giống nhưng thông tin mới, bỏ qua cả
        # phép so cả câu lẫn so từng câu với lượt đáp này.
        if d_digits != _digits(prev):
            continue
        p = normalize(prev)
        if SequenceMatcher(None, d, p).ratio() >= ratio:
            return True
        p_sents = [normalize(s) for s in sentences(prev)]
        for sent in d_sents:
            if len(sent.split()) >= min_words and any(
                SequenceMatcher(None, sent, s).ratio() >= ratio for s in p_sents
            ):
                return True
    return False
