"""Phép kiểm câu trả lời cuối của Orchestrator (spec §7, docs/architech-agent.md
luật 4-5) — code thuần, không gọi model, không regex.

`check_reply` trả mã vi phạm theo thứ tự cố định `CHECK_CODES`, mỗi mã tối đa
một lần; rỗng = câu được gửi khách. `strip_tool_text` xoá tên tool / khối JSON
lọt vào câu TRƯỚC khi kiểm.

Các tập từ ở đây là từ NGỮ PHÁP của câu bot (động từ "đặt / giữ / dời / huỷ",
"đã / rồi", phủ định "chưa / không", đại từ xưng hô) — guard đọc câu BOT nói về
việc của chính nó, không đoán ý định của khách (CONTEXT.md bẫy 21).
"""
from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from app.agents.salon.guard.facts import (
    ClockKey,
    ReplyFacts,
    clocks_in_text,
    is_known,
)
from app.agents.salon.shared.text_scan import (
    digits_in,
    scan_money,
    split_clauses,
    split_sentences,
    words,
)
from app.agents.salon.shared.tool_names import CONTROL_TOOLS, SUBAGENT_TOOLS
from app.core.config import settings
from app.core.text import normalize_for_match

CHECK_CODES = (
    "claim", "unknown_time", "unknown_price", "missing_fact", "missing_question",
    "missing_phone", "phone_not_allowed", "pending_undecided", "pronoun", "repeat",
)

REPEAT_RATIO = 0.85
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 2

# ---------------------------------------------------------------------------
# claim — nhận vơ đã đặt / dời / huỷ, giữ chỗ không có thật
# ---------------------------------------------------------------------------

# Động từ cam kết -> loại việc đã ghi cho phép nói nó. "đặt / ghi / chốt" cũng
# đúng sau một lần dời đã duyệt (lịch mới đã được ghi); "đổi" cũng đúng sau một
# lần đặt ("giờ đặt đã đổi thành …").
_CLAIMS_FOR_VERB = {
    "đặt": ("booked", "moved"),
    "ghi": ("booked", "moved"),
    "chốt": ("booked", "moved"),
    "dời": ("moved",),
    "đổi": ("moved", "booked"),
    "hủy": ("cancelled",),
    "huỷ": ("cancelled",),
}
_HOLD = "giữ"
_DONE_BEFORE = frozenset({"đã", "vừa", "đang"})
_DONE_AFTER = frozenset({"rồi", "xong"})
_NEGATION = frozenset({"chưa", "không", "ko"})
# "có người đặt rồi": người khác làm, không phải em nhận.
_OTHER_SUBJECTS = frozenset({"người", "khách"})
# "sẽ giữ / sẽ huỷ": hứa làm, chưa phải nói đã làm.
_FUTURE = frozenset({"sẽ"})
# "ghi nhận / ghi lại" không phải ghi lịch.
_NOT_BOOKING_AFTER_GHI = frozenset({"nhận", "lại"})
# "đã đổi ý rồi" không nói về lịch — dời/đổi/huỷ cần mệnh đề nói tới lịch.
_NEEDS_APPOINTMENT = frozenset({"dời", "đổi", "hủy", "huỷ"})
_APPOINTMENT_WORDS = frozenset({"lịch", "chỗ", "hẹn"})
# Cụm tách mệnh đề: "…, còn …" là hai việc khác nhau.
_CLAUSE_BREAKS = ("còn",)


def _claim_verbs(clause_words: list[str], has_clock: bool) -> set[str]:
    """Động từ cam kết mà mệnh đề nói là em ĐÃ / ĐANG làm: có "đã / vừa /
    đang" trong 2 từ trước, hoặc "rồi / xong" phía sau; riêng "giữ" còn tính ở
    dạng trần ("em giữ chỗ 4 giờ chiều") khi mệnh đề nói tới lịch hay giờ."""
    if any(word in _NEGATION for word in clause_words):
        return set()
    about_appointment = has_clock or any(word in _APPOINTMENT_WORDS for word in clause_words)
    verbs = set()
    for i, word in enumerate(clause_words):
        if word != _HOLD and word not in _CLAIMS_FOR_VERB:
            continue
        before = set(clause_words[max(0, i - 2):i])
        if before & (_OTHER_SUBJECTS | _FUTURE):
            continue
        after = clause_words[i + 1:]
        if word == "ghi" and after[:1] and after[0] in _NOT_BOOKING_AFTER_GHI:
            continue
        done = bool(before & _DONE_BEFORE) or any(w in _DONE_AFTER for w in after)
        if not (done or (word == _HOLD and about_appointment)):
            continue
        if word in _NEEDS_APPOINTMENT and not about_appointment:
            continue
        verbs.add(word)
    return verbs


def _commit_ok(verb: str, said: list[ClockKey], facts: ReplyFacts) -> bool:
    kinds = _CLAIMS_FOR_VERB[verb]
    done: set[ClockKey] = set().union(*(facts.done_claims.get(kind, set()) for kind in kinds))
    if not done:
        return False
    if all(is_known(clock, done) for clock in said):
        return True
    # "đã dời lịch từ 3 giờ sang 4 giờ": giờ cũ của lịch vừa dời không phải
    # giờ đã ghi, nhưng là giờ có thật — hợp lệ khi mệnh đề có giờ mới đã dời.
    moved = facts.done_claims.get("moved", set())
    return ("moved" in kinds and any(is_known(clock, moved) for clock in said)
            and all(is_known(clock, done) or is_known(clock, facts.clocks) for clock in said))


def _clause_claims_falsely(clause: str, facts: ReplyFacts) -> bool:
    said = clocks_in_text(clause)
    verbs = _claim_verbs(words(normalize_for_match(clause)), bool(said))
    if not verbs:
        return False
    commit = verbs - {_HOLD}
    if commit:
        return not all(_commit_ok(verb, said, facts) for verb in commit)
    # Chỉ "giữ": hợp lệ khi giờ đó đang có đề xuất chờ thật (hoặc đã ghi).
    holdable = facts.held_clocks.union(*facts.done_claims.values())
    if said:
        return not all(is_known(clock, holdable) for clock in said)
    return not holdable


def _has_false_claim(text: str, facts: ReplyFacts) -> bool:
    return any(
        _clause_claims_falsely(clause, facts)
        for sentence in split_sentences(text)
        for clause in split_clauses(sentence, break_words=_CLAUSE_BREAKS)
    )


# ---------------------------------------------------------------------------
# unknown_price
# ---------------------------------------------------------------------------

# Hai số tiền nối bằng các từ này là hai giá riêng ("gel 150 nghìn và gội 60
# nghìn"), không phải một khoảng.
_PRICE_BREAKS = ("và", "với", "còn")


def _has_unknown_price(text: str, facts: ReplyFacts) -> bool:
    """Mọi số tiền trong một mệnh đề phải cùng nằm trong MỘT dải giá — một
    khoảng ("từ 120 tới 180 nghìn") thì cả hai đầu trong cùng một dải."""
    for sentence in split_sentences(text):
        for clause in split_clauses(sentence, break_words=_PRICE_BREAKS):
            amounts = scan_money(clause)
            if amounts and not any(
                all(low <= amount <= high for amount in amounts) for low, high in facts.price_ranges
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# pronoun — CONTEXT.md mục Xưng hô
# ---------------------------------------------------------------------------

# Giọng cô / chú / bác: cấm ở mọi nơi khách thấy.
_ELDER_WORDS = frozenset({"cô", "chú", "bác"})
# "con / cháu" làm CHỦ NGỮ của việc lễ tân ("con xem lịch", "để con giữ chỗ") là
# bot tự xưng sai; "cho con gái chị" là con của khách — không bắt.
_CHILD_SPEAKERS = frozenset({"con", "cháu"})
_RECEPTION_VERBS = frozenset({
    "xem", "giúp", "đặt", "hỏi", "kiểm", "giữ", "chào", "xin", "cảm", "báo", "nhắn", "ghi",
    "dời", "huỷ", "hủy",
})
_SELF_WRONG = frozenset({"tôi"})
_GENDERED = frozenset({"anh", "chị"})
_OTHER_GENDER = {"anh": "chị", "chị": "anh"}


def _cased_tokens(sentence: str) -> list[str]:
    return "".join(ch if ch.isalnum() else " " for ch in sentence).split()


def _addresses_customer(lower: list[str], cased: list[str], i: int, name: list[str]) -> bool:
    """Từ ở vị trí `i` đang GỌI khách: đầu câu (sau "Dạ"), trước "ơi", hay
    ngay trước tên khách / một tên viết hoa."""
    nxt = lower[i + 1] if i + 1 < len(lower) else None
    if i == 0 or (i == 1 and lower[0] == "dạ") or nxt == "ơi":
        return True
    if name and [w.lower() for w in cased[i + 1:i + 1 + len(name)]] == name:
        return True
    return False


def _sentence_has_wrong_pronoun(sentence: str, address_words: list[str]) -> bool:
    # NFC nhưng giữ hoa/thường (cần để nhận tên) — khác `normalize_for_match`.
    cased = _cased_tokens(unicodedata.normalize("NFC", sentence))
    lower = [w.lower() for w in cased]
    both = address_words[:2] == ["anh", "chị"]
    own = address_words[0] if address_words and address_words[0] in _GENDERED and not both else None
    name = address_words[1:] if own else []
    for i, word in enumerate(lower):
        prev = lower[i - 1] if i > 0 else None
        nxt = lower[i + 1] if i + 1 < len(lower) else None
        if word in _SELF_WRONG:
            return True
        if word in _CHILD_SPEAKERS and nxt in _RECEPTION_VERBS:
            return True
        if word in _ELDER_WORDS and (
            _addresses_customer(lower, cased, i, name)
            or (i + 1 < len(cased) and cased[i + 1][:1].isupper())
        ):
            return True
        if word not in _GENDERED or nxt == "chủ":   # "chị chủ" là chủ tiệm
            continue
        if both:
            # "anh chị": một mình "anh" hay "chị" là đoán giới tính.
            if not ((word == "anh" and nxt == "chị") or (word == "chị" and prev == "anh")):
                return True
        elif own and word == _OTHER_GENDER[own] and _addresses_customer(lower, cased, i, name):
            return True
    return False


def _has_wrong_pronoun(text: str, address: str) -> bool:
    address_words = normalize_for_match(address).split()
    return any(_sentence_has_wrong_pronoun(s, address_words) for s in split_sentences(text))


# ---------------------------------------------------------------------------
# repeat
# ---------------------------------------------------------------------------


def _normalized_sentence(sentence: str) -> str:
    return " ".join(words(normalize_for_match(sentence)))


def _repeats(text: str, previous_replies: list[str]) -> bool:
    """Một câu (≥ 6 từ) giống ≥ 0.85 một câu trong 2 câu trả lời gần nhất. Bộ
    chữ số của câu trả lời khác câu trước (giờ / ngày / giá mới) → không phải
    lặp: cùng khung câu nhưng thông tin mới."""
    digits = set(digits_in(text))
    sentences = [_normalized_sentence(s) for s in split_sentences(text)]
    for previous in previous_replies[-REPEAT_LOOKBACK:]:
        if set(digits_in(previous)) != digits:
            continue
        before = [_normalized_sentence(s) for s in split_sentences(previous)]
        for sentence in sentences:
            if len(sentence.split()) >= REPEAT_MIN_WORDS and any(
                SequenceMatcher(None, sentence, old).ratio() >= REPEAT_RATIO for old in before
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# check_reply
# ---------------------------------------------------------------------------


def _phone_digits() -> str:
    return "".join(digits_in(settings.shop_phone or ""))


def _mentions_phone(text: str) -> bool:
    phone = _phone_digits()
    return bool(phone) and phone in "".join(digits_in(text))


def check_reply(text: str, facts: ReplyFacts) -> list[str]:
    """Mã vi phạm của `text` so với dữ kiện của lượt, theo thứ tự `CHECK_CODES`."""
    text = text or ""
    found: set[str] = set()
    if _has_false_claim(text, facts):
        found.add("claim")
    if not all(is_known(clock, facts.clocks) for clock in clocks_in_text(text)):
        found.add("unknown_time")
    if _has_unknown_price(text, facts):
        found.add("unknown_price")
    normalized = normalize_for_match(text)
    if any(normalize_for_match(phrase) not in normalized for phrase in facts.must_mention):
        found.add("missing_fact")
    if facts.question_required and "?" not in text:
        found.add("missing_question")
    said_phone = _mentions_phone(text)
    if facts.needs_phone and _phone_digits() and not said_phone:
        found.add("missing_phone")
    if not facts.needs_phone and said_phone:
        found.add("phone_not_allowed")
    if facts.pending_undecided:
        found.add("pending_undecided")
    if _has_wrong_pronoun(text, facts.address):
        found.add("pronoun")
    if _repeats(text, facts.previous_replies):
        found.add("repeat")
    return [code for code in CHECK_CODES if code in found]


# ---------------------------------------------------------------------------
# strip_tool_text
# ---------------------------------------------------------------------------

_TOOL_NAMES = sorted(CONTROL_TOOLS | SUBAGENT_TOOLS, key=len, reverse=True)
_BRACKETS = {"(": ")", "[": "]"}
_TIGHT_PUNCT = ".,!?…;:"


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _drop_json(text: str) -> str:
    """Bỏ mọi khối `{...}` cân bằng (kể cả lồng nhau); `{` không đóng thì giữ."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, j = 0, i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j < n:
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _tool_name_at(text: str, i: int) -> Optional[int]:
    """Vị trí kết thúc của tên tool bắt đầu ĐÚNG ở `i` (đứng riêng một từ)."""
    if i > 0 and _is_word_char(text[i - 1]):
        return None
    for name in _TOOL_NAMES:
        end = i + len(name)
        if text.startswith(name, i) and (end == len(text) or not _is_word_char(text[end])):
            return end
    return None


def _drop_tool_names(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = _tool_name_at(text, i)
        if end is None:
            out.append(text[i])
            i += 1
            continue
        if text.startswith("()", end):
            end += 2
        if out and out[-1] == "`" and end < n and text[end] == "`":
            out.pop()
            end += 1
        i = end
    return "".join(out)


def _drop_empty_brackets(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        close = _BRACKETS.get(text[i])
        if close is not None:
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] == close:
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _tidy_line(line: str) -> str:
    out: list[str] = []
    for ch in " ".join(line.split()):
        if ch in _TIGHT_PUNCT and out and out[-1] == " ":
            out.pop()
        if ch == "," and out and out[-1] == ",":
            continue
        out.append(ch)
    return "".join(out).strip()


def strip_tool_text(text: str) -> str:
    """Bỏ tên tool kỹ thuật (`CONTROL_TOOLS` + tool agent con) và khối JSON
    `{...}` khỏi câu sắp gửi khách, rồi gộp khoảng trắng. Câu sạch trả NGUYÊN
    VĂN (kể cả xuống dòng)."""
    if not text:
        return text
    cleaned = _drop_empty_brackets(_drop_tool_names(_drop_json(text)))
    if cleaned == text:
        return text
    return "\n".join(_tidy_line(line) for line in cleaned.split("\n")).strip()
