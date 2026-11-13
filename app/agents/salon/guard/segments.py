"""Schema + cú pháp của câu trả lời cuối Level C (spec level C §2-§3, Task 2):
`REPLY_SCHEMA` ép câu trả lời thành một mảng đoạn (`Segment`), mỗi đoạn hoặc là
chữ thường (`ref`/`role` null) hoặc trích một dữ kiện trong `FactCatalog`
(Task 1) bằng id + vai trò. `parse_reply` đọc JSON thô về `list[Segment]`
(hoặc `None` nếu sai hình dạng); `join_segments` ráp lại thành một câu để log/
hiển thị; `check_segments` so từng đoạn có trích dẫn với `FactCatalog`, trả
`SegmentReport` nêu lỗi bằng tiếng Anh (cho gợi ý viết lại ở prompt, tiếng Anh
theo Global Constraints).

`ReplyGuardMiddleware` dùng chúng qua `checks.check_structured`; Orchestrator
gắn `REPLY_SCHEMA` qua `orchestrator/agent.py::reply_format`.

Không regex (gói `salon` không `import re`, `tests/test_regex_budget.py`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

from pydantic import BaseModel

from app.agents.salon.guard.catalog import CatalogEntry, FactCatalog, SEGMENT_ROLES
from app.agents.salon.guard.clocks import clocks_in_text
from app.agents.salon.shared.text_scan import scan_money, words
from app.core.text import normalize_for_match

REPLY_SCHEMA = {
    "title": "Reply", "type": "object", "additionalProperties": False, "required": ["segments"],
    "properties": {"segments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "ref", "role"],
        "properties": {
            "text": {"type": "string"},
            "ref": {"type": ["string", "null"]},
            "role": {"anyOf": [{"type": "string", "enum": list(SEGMENT_ROLES)}, {"type": "null"}]},
        }}}},
}

# Từ còn lại hợp lệ trong một đoạn giờ sau khi lọc hết số và "Nh" dính liền
# (bước 3.5.1 brief) — đủ để nói mọi cách người Việt nói giờ/ngày tương đối,
# không hơn (dư từ nào khác là coi như đoạn có "rác" ngoài dữ kiện).
TIME_WORDS = frozenset({
    "giờ", "h", "rưỡi", "kém", "phút", "sáng", "trưa", "chiều", "tối", "đêm", "mai", "hôm", "nay",
    "ngày", "kia", "mốt", "thứ", "hai", "ba", "tư", "năm", "sáu", "bảy", "chủ", "nhật", "cn", "tuần",
    "này", "sau", "tới", "lúc", "vào", "tháng",
})

_SENTENCE_PUNCT = (".", "?", "!")
# Dấu kết câu dùng cho `taken_not_told` — thêm "…" (brief liệt kê cả dấu này).
_SENTENCE_END = (".", "?", "!", "…")

# Cụm từ (của câu BOT, không đoán ý khách — Global Constraints) báo một giờ đã
# kín: live run 5, turn 7 nói "3 giờ chiều" là `taken` nhưng câu không có cụm
# nào trong đây, lại còn mời khách chọn giờ đó.
TAKEN_MARKERS: tuple[tuple[str, ...], ...] = (
    ("kín",), ("có", "khách"), ("có", "người"), ("được", "đặt"), ("hết", "chỗ"),
    ("không", "còn", "trống"), ("không", "trống"), ("bận",),
)
# Cụm mời khách chọn giờ — không được xuất hiện trong MỆNH ĐỀ giữ đoạn `taken`.
OFFER_WORDS: tuple[tuple[str, ...], ...] = (("chọn",), ("lấy",), ("đồng", "ý"))


class Segment(BaseModel):
    text: str
    ref: Optional[str]
    role: Optional[str]


def parse_reply(raw: str) -> Optional[list[Segment]]:
    """`json.loads` rồi kiểm hình dạng tay (không chỉ dựa pydantic) để một
    `role` ngoài `SEGMENT_ROLES` hay thiếu khoá cũng ra `None` thay vì ném lỗi."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("segments")
    if not isinstance(items, list):
        return None

    segments: list[Segment] = []
    for item in items:
        if not isinstance(item, dict) or set(item.keys()) != {"text", "ref", "role"}:
            return None
        text, ref, role = item["text"], item["ref"], item["role"]
        if not isinstance(text, str):
            return None
        if ref is not None and not isinstance(ref, str):
            return None
        if role is not None and role not in SEGMENT_ROLES:
            return None
        segments.append(Segment(text=text, ref=ref, role=role))
    return segments


def _collapse_spaces(text: str) -> str:
    out: list[str] = []
    prev_space = False
    for ch in text:
        if ch == " ":
            if prev_space:
                continue
            prev_space = True
        else:
            prev_space = False
        out.append(ch)
    return "".join(out)


def join_segments(segments: Sequence[Segment]) -> str:
    parts: list[str] = []
    for segment in segments:
        text = segment.text
        if parts and parts[-1] and text and parts[-1][-1].isalnum() and text[0].isalnum():
            parts.append(" ")
        parts.append(text)
    return _collapse_spaces("".join(parts)).strip()


@dataclass
class SegmentReport:
    codes: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    cited: list[tuple[str, str]] = field(default_factory=list)


def _is_digit_h_token(token: str) -> bool:
    """`'9h'`, `'9h30'`: đúng một `h`, phần trước là số, phần sau rỗng hoặc số."""
    idx = token.find("h")
    if idx <= 0:
        return False
    left, right = token[:idx], token[idx + 1:]
    if "h" in right:
        return False
    if not left.isdigit():
        return False
    return right == "" or right.isdigit()


def _clock_text_matches(entry: CatalogEntry, text: str) -> bool:
    readings_list = clocks_in_text(text)
    if not readings_list:
        return False
    known = set(entry.clocks)
    if not all(reading & known for reading in readings_list):
        return False
    for token in words(normalize_for_match(text)):
        if token.isdigit() or _is_digit_h_token(token) or token in TIME_WORDS:
            continue
        return False
    return not any(punct in text for punct in _SENTENCE_PUNCT)


def _contains_word_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """True nếu `needle` là một dãy từ liên tiếp nằm trong `haystack` — khớp
    tên dịch vụ theo cụm từ, không phải chuỗi con (bước Finding 1: "Uốn" không
    được khớp bên trong "muốn"/"uống")."""
    if not needle:
        return False
    n = len(needle)
    for i in range(len(haystack) - n + 1):
        if haystack[i:i + n] == list(needle):
            return True
    return False


def _service_text_matches(entry: CatalogEntry, text: str, catalog: FactCatalog) -> bool:
    text_words = words(normalize_for_match(text))
    own_words = words(normalize_for_match(entry.names[0]))
    if not (set(text_words) & set(own_words)):
        return False
    own_names = {normalize_for_match(name) for name in entry.names}
    for other in catalog.entries:
        if other.kind != "service" or other.id == entry.id:
            continue
        for name in other.names:
            normalized_name = normalize_for_match(name)
            if normalized_name in own_names:
                continue
            if _contains_word_sequence(text_words, words(normalized_name)):
                return False
    return True


def _price_text_matches(entry: CatalogEntry, text: str) -> bool:
    amounts = scan_money(text)
    if not amounts or entry.price is None:
        return False
    low, high = entry.price
    if not all(low <= amount <= high for amount in amounts):
        return False
    if "?" in text or "!" in text:
        return False
    return not text.endswith(".")


def _text_matches(entry: CatalogEntry, role: str, text: str, catalog: FactCatalog) -> bool:
    if entry.kind in ("time", "customer_clock"):
        return _clock_text_matches(entry, text)
    if role == "service":
        return _service_text_matches(entry, text, catalog)
    if role == "price":
        return _price_text_matches(entry, text)
    return True


def _plain_segment_leaks(text: str, catalog: FactCatalog) -> bool:
    if clocks_in_text(text):
        return True
    if scan_money(text):
        return True
    text_words = words(normalize_for_match(text))
    for entry in catalog.entries:
        if entry.kind != "service":
            continue
        for name in entry.names:
            if _contains_word_sequence(text_words, words(normalize_for_match(name))):
                return True
    return False


def check_segments(segments: Sequence[Segment], catalog: FactCatalog) -> SegmentReport:
    report = SegmentReport()

    # Bước 4 brief: gộp dịch vụ của mọi đề xuất đang giữ chỗ TRƯỚC vòng lặp
    # chính — đoạn dịch vụ có thể đứng trước đoạn "held" trong câu trả lời.
    held_services: set[str] = set()
    for segment in segments:
        if segment.ref is not None and segment.role == "held":
            entry = catalog.get(segment.ref)
            if entry is not None:
                held_services.update(entry.service_ids)

    ids = [entry.id for entry in catalog.entries]

    for segment in segments:
        text, ref, role = segment.text, segment.ref, segment.role

        if (ref is None) != (role is None):
            report.codes.add("bad_structure")
            report.problems.append(f"segment {text!r} must have both ref and role, or neither")
            continue

        if ref is None:
            if _plain_segment_leaks(text, catalog):
                report.codes.add("unreferenced")
                report.problems.append(
                    "write each time, service name and price as its own segment with ref and role"
                )
            continue

        entry = catalog.get(ref)
        if entry is None:
            report.codes.add("unknown_ref")
            report.problems.append(f"{ref} is not in Reply facts (valid ids: {', '.join(ids)})")
            continue

        if role not in entry.roles:
            report.codes.add("wrong_role")
            report.problems.append(f"{ref} allows {', '.join(entry.roles)}, not {role}")
            continue

        if (role == "service" and held_services and entry.service_ids
                and entry.service_ids[0] not in held_services):
            report.codes.add("wrong_role")
            report.problems.append(f"{ref} is not a service of the held proposal")
            continue

        if not _text_matches(entry, role, text, catalog):
            report.codes.add("ref_mismatch")
            report.problems.append(
                f"{ref} is {entry.label}; the segment text {text!r} does not match it"
            )
            continue

        report.cited.append((ref, role))

    return report


# ---------------------------------------------------------------------------
# taken_not_told — spec live run 5: một đoạn trích đúng vai `taken` vẫn phải
# được CÂU nói là đã kín, và mệnh đề chứa nó không được mời khách chọn giờ đó.
# ---------------------------------------------------------------------------


def _plain_pieces(text: str) -> list[tuple[str, bool]]:
    """Tách `text` của MỘT đoạn chữ thường tại mỗi dấu kết câu (`_SENTENCE_END`,
    theo sau là khoảng trắng hoặc hết chuỗi) — một đoạn chữ thường có thể mang
    nhiều câu (vd "... nhé. Chị chọn ..."); đoạn cuối cùng (`ends=False`) nếu
    không kết thúc bằng dấu đó thì câu còn mở, nối tiếp qua các đoạn sau."""
    pieces: list[tuple[str, bool]] = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        if text[i] in _SENTENCE_END and (i + 1 == n or text[i + 1].isspace()):
            pieces.append((text[start:i + 1], True))
            start = i + 1
        i += 1
    if start < n:
        pieces.append((text[start:], False))
    return pieces


_Token = tuple[str, Optional[str], Optional[str]]  # (text, ref, role)


def _group_into_sentences(segments: Sequence[Segment]) -> list[list[_Token]]:
    """Gom các đoạn thành câu (quy tắc 1 của brief): đoạn trích dữ kiện không
    bao giờ tách câu; đoạn chữ thường có thể mang nhiều câu, tách tại mỗi dấu
    kết câu của chính nó."""
    sentences: list[list[_Token]] = []
    current: list[_Token] = []
    for segment in segments:
        if segment.ref is None:
            for piece, ends in _plain_pieces(segment.text):
                current.append((piece, None, None))
                if ends:
                    sentences.append(current)
                    current = []
        else:
            current.append((segment.text, segment.ref, segment.role))
    if current:
        sentences.append(current)
    return sentences


def _find_char(text: str, chars: str, *, from_end: bool = False) -> Optional[int]:
    positions = [i for i, ch in enumerate(text) if ch in chars]
    if not positions:
        return None
    return positions[-1] if from_end else positions[0]


def _clause_around(sentence: list[_Token], index: int) -> str:
    """Mệnh đề (quy tắc 2b): chữ từ dấu `,`/`;` gần nhất TRƯỚC đoạn `taken` tới
    dấu `,`/`;` gần nhất SAU nó, trong câu."""
    before = "".join(text for text, _, _ in sentence[:index])
    after = "".join(text for text, _, _ in sentence[index + 1:])
    cut_before = _find_char(before, ",;", from_end=True)
    cut_after = _find_char(after, ",;")
    before_clause = before[cut_before + 1:] if cut_before is not None else before
    after_clause = after[:cut_after] if cut_after is not None else after
    return before_clause + sentence[index][0] + after_clause


def taken_not_told(segments: Sequence[Segment], catalog: FactCatalog) -> list[str]:
    """Mọi ref được trích vai `taken` mà câu của nó (a) không có cụm báo kín
    (`TAKEN_MARKERS`) hoặc (b) mệnh đề giữ nó lại mời khách chọn (`OFFER_WORDS`)
    — live run 5, turn 7: "3 giờ chiều" gắn đúng vai `taken` nhưng câu mời
    khách chọn chính giờ đó, không nói nó đã kín."""
    broken: list[str] = []
    for sentence in _group_into_sentences(segments):
        sentence_text = "".join(text for text, _, _ in sentence)
        sentence_words = words(normalize_for_match(sentence_text))
        has_marker = any(_contains_word_sequence(sentence_words, marker) for marker in TAKEN_MARKERS)
        for index, (_, ref, role) in enumerate(sentence):
            if role != "taken" or ref is None or catalog.get(ref) is None:
                continue
            clause_words = words(normalize_for_match(_clause_around(sentence, index)))
            has_offer = any(_contains_word_sequence(clause_words, offer) for offer in OFFER_WORDS)
            if (not has_marker or has_offer) and ref not in broken:
                broken.append(ref)
    return broken
