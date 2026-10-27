# Cắt giảm Regex trong AI Agent Pipeline (45 → 3 regex) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cắt giảm số lượng biểu thức chính quy (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`) từ 45 xuống còn đúng 3 regex (-93,3%) bằng cách mở rộng `TurnAnalysis`/`ParsedTime` và dùng module xử lý token thuần Python (`tokens.py`), giữ xanh toàn bộ 1.657 backend tests và không tăng số lượt gọi LLM.

**Architecture:** Thay toàn bộ các mẫu regex biên từ `(?<!\w)...(?!\w)`, tách câu/mệnh đề và dò giờ/ngày tiếng Việt ở `relatives.py` (6→0), `reply.py` (14→0), `tools.py` (1→0), `timeparse.py` (3→1) và `guard.py` (21→2) bằng một module thuần Python `app/agents/booking_graph/tokens.py` (0 regex) kết hợp các trường cấu trúc mới `TurnAnalysis.aside`, `TurnAnalysis.mentioned_times` và `ParsedTime.has_explicit_day`/`has_explicit_clock`. Giữ nguyên 3 regex có lý do kỹ thuật rõ ràng: `_FULL` trong `timeparse.py` (bẫy #11 `CONTEXT.md`) và `_TOOL_GROUP`, `_TOOL_BARE` trong `guard.py` (xoá tên tool snake_case).

**Tech Stack:** Python 3.12, Pydantic v2, LangChain 1.4.6, LangGraph 1.2.16, Motor / PyMongo, pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-27-regex-reduction-design.md`.

## Global Constraints

- Đúng **1 worker** uvicorn; **chỉ chạy MỘT tiến trình `pytest` một lúc** (chung DB `chatbot_test_db`).
- Worktree làm việc: `/home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b` (nhánh `feat/target-architecture`).
- Lệnh chạy test chuẩn (từ gốc worktree `salona-booking-harness-b`):
  `PYTHONPATH=. .venv/bin/python -m pytest -q <path>`
- **Mọi logic đặt lịch ở `app/services/`**; mọi đường ghi lịch đi qua `ProposalService.approve`; agent không có tool ghi (`tests/test_write_fence.py` phải xanh).
- **Chỉ stream token tag `respond`**; `analyze`, `classify`, `timeparse`, `rewrite` luôn `streaming=False`.
- Prompt phân loại (`ANALYZE_PROMPT`, `CLASSIFY_PROMPT`, `SUPERVISOR_PROMPT`) viết bằng **tiếng Anh**, **không có câu mẫu tiếng Việt và không chứa ký tự tiếng Việt có dấu** ngoài `"chị"` và `"anh"` ở `ANALYZE_PROMPT` (bẫy #15, #16 — `tests/test_prompts.py` canh).
- Chuỗi giao diện và các câu fallback của `confirm`/`guard` giữ nguyên từng chữ.
- Không đụng tới 13 regex bảo mật/PII/SĐT/tự xưng ở `app/core/phone.py` (1), `app/core/text.py` (8) và `app/services/conversation_state.py` (4).
- Docs/comment bằng tiếng Việt; commit message bằng tiếng Anh ngắn gọn, kết bằng trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Hành động | Trách nhiệm | Số `re.compile` sau khi xong |
|---|---|---|---:|
| `app/agents/booking_graph/tokens.py` | Create | Tách từ/câu/mệnh đề, khớp cụm từ biên từ (`padded`, `has_phrase`), quét giờ đọc tiếng Việt (`scan_spoken_clocks`) và quét mốc thời gian (`scan_mentioned_times`, `names_a_day`, `names_a_clock`) bằng Python thuần | **0** |
| `app/agents/booking_graph/analyzer.py` | Modify | Thêm `MentionedTime`, `TurnAnalysis.aside`, `TurnAnalysis.mentioned_times` và lọc/chuẩn hoá trong `sanitize_analysis` | **0** |
| `app/agents/booking_graph/prompts.py` | Modify | Cập nhật `ANALYZE_PROMPT` mô tả `aside` và `mentioned_times` bằng tiếng Anh ASCII | **0** |
| `app/agents/booking_graph/relatives.py` | Modify | Thay 6 regex bằng `tokens.py` + hỗ trợ đọc `TurnAnalysis.aside` | **0** (giảm 6) |
| `app/agents/booking_graph/supervisor.py` | Modify | Giữ nguyên lối tắt code qua `relatives.py` và `reply.py` (nay 0 regex) | **0** |
| `app/agents/booking_graph/reply.py` | Modify | Thay 14 regex bằng `tokens.py` + đối chiếu `TurnAnalysis.mentioned_times` | **0** (giảm 14) |
| `app/agents/booking_graph/tools.py` | Modify | Thay `_DATE_ONLY` bằng `_is_date_only` dùng `date.fromisoformat` | **0** (giảm 1) |
| `app/agents/booking_graph/timeparse.py` | Modify | Thêm `has_explicit_day`, `has_explicit_clock` vào `ParsedTime`; thay `_NAMES_A_DAY`, `_NAMES_A_CLOCK` bằng `tokens.names_a_day`/`names_a_clock`; giữ `_FULL` | **1** (giảm 2) |
| `app/agents/booking_graph/guard.py` | Modify | Thay 19 regex ngữ nghĩa/giọng bằng `tokens.py`; chỉ giữ `_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names` | **2** (giảm 19) |
| `tests/test_tokens.py` | Create | Kiểm thử đơn vị cho `tokens.py` và rào chắn `0 regex` trong `tokens.py` | — |
| `tests/test_regex_budget.py` | Create | Rào chắn AST/source khoá chặt số lượng `re.compile` của từng file trong `app/agents/booking_graph/` (tổng ≤ 3) | — |

---

### Task 1: Module xử lý token và thời gian thuần Python (`tokens.py`)

**Files:**
- Create: `app/agents/booking_graph/tokens.py`
- Test: `tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `words(text: str) -> List[str]`: chữ thường, tách mọi chuỗi chữ/số liên tiếp (`ch.isalnum()`), bỏ dấu gạch dưới `_`.
  - `cased_words(text: str) -> List[str]`: giữ nguyên chữ hoa/thường, tách mọi chuỗi chữ/số liên tiếp.
  - `unaccented_words(text: str) -> List[str]`: như `words(text)` nhưng từ nào có dấu tiếng Việt thì thay bằng `"_"` (giữ lại đúng những từ khách gõ không dấu).
  - `padded(text: str) -> str`: `" " + " ".join(words(text)) + " "` (luôn bắt đầu và kết thúc bằng `" "`).
  - `padded_unaccented_only(text: str) -> str`: `" " + " ".join(unaccented_words(text)) + " "`.
  - `has_phrase(padded_text: str, phrases: Iterable[str]) -> bool`: `any(f" {p} " in padded_text for p in phrases)`.
  - `digits_in(text: str) -> List[str]`: danh sách các cụm chữ số ASCII liên tiếp trong `text` theo thứ tự xuất hiện.
  - `split_sentences(text: str) -> List[str]`: tách câu theo dấu `.!?…` khi theo sau là khoảng trắng hoặc hết chuỗi.
  - `split_clauses(text: str, *, break_words: Sequence[str] = ()) -> List[str]`: tách mệnh đề theo `;`, `.`, `!`, `?`, `…`, `\n`, dấu `,` (trừ dấu phẩy đứng ngay trước chữ số kiểu `"Thứ Tư 21/10, 9 giờ sáng"`), và các từ nối trong `break_words` (vd `("còn", "con")`).
  - `scan_spoken_clocks(text: str) -> List[Tuple[int, int, Optional[str]]]`: trả danh sách `(hour, minute, period)` cho mọi cụm giờ đọc kiểu `"9 giờ sáng"`, `"3 giờ rưỡi chiều"`, `"3 giờ 15 chiều"` trong `text`.
  - `scan_times(text: str) -> List[Tuple[int, Optional[str], Optional[int]]]`: trả danh sách `(hour, period, minute)` cho mọi mốc giờ khách viết (`"3 giờ chiều"`, `"15h30"`, `"15:30"`, `"3 rưỡi"`, `"4 gio"`, `"16h"`).
  - `scan_day_offsets(text: str) -> List[int]`: trả danh sách offset ngày tương đối (`0` cho hôm nay/bữa nay/nay, `1` cho ngày mai/mai, `2` cho ngày kia/mốt).
  - `scan_weekdays(text: str) -> List[int]`: trả danh sách thứ trong tuần (`0` = Thứ Hai .. `6` = Chủ Nhật).
  - `has_next_week(text: str) -> bool`: `True` khi có `"tuần sau"` / `"tuần tới"` (kể cả không dấu).
  - `names_a_day(text: str) -> bool`: `True` khi câu có nêu ngày, thứ, tuần, tháng, hôm nay/mai/mốt/kia hoặc `dd/mm`.
  - `names_a_clock(text: str) -> bool`: `True` khi câu có nêu một giờ bằng số (`"4 giờ"`, `"16h"`, `"15h30"`, `"3 rưỡi"`, `"15:30"`).

- [ ] **Step 1: Write the failing test (`tests/test_tokens.py`)**

```python
from app.agents.booking_graph import tokens


def test_tokens_module_contains_no_regex_imports_or_calls():
    import inspect
    source = inspect.getsource(tokens)
    assert "import re" not in source and "re.compile" not in source


def test_words_and_cased_words_split_punctuation_cleanly():
    assert tokens.words("Dạ, anh Tám! 15:30 nhé?") == ["dạ", "anh", "tám", "15", "30", "nhé"]
    assert tokens.cased_words("Dạ, Chú Tám ơi!") == ["Dạ", "Chú", "Tám", "ơi"]
    # Dấu câu sinh vách ngăn `|` trong padded để cụm nhiều từ không nhảy qua dấu phẩy/chấm
    p = tokens.padded("mất bao, lâu hả chị")
    assert tokens.has_phrase(p, ("bao lâu",)) is False
    assert tokens.has_phrase(p, ("bao",)) is True


def test_unaccented_words_masks_accented_tokens():
    assert tokens.unaccented_words("dạ Hồng khong đi") == ["_", "_", "khong", "_"]


def test_has_phrase_matches_whole_words_and_multiword_phrases():
    p = tokens.padded("ok em, việc gì cũng phải tính trước cho chắc")
    assert tokens.has_phrase(p, ("ok",)) is True
    assert tokens.has_phrase(p, ("k",)) is False
    assert tokens.has_phrase(p, ("tính trước",)) is True
    assert tokens.has_phrase(p, ("tính sau",)) is False


def test_split_sentences_and_clauses_preserve_date_commas():
    text = "Em đang giữ Thứ Tư 21/10, 9 giờ sáng. Còn 2 giờ chiều em chưa giữ ạ!"
    sents = tokens.split_sentences(text)
    assert sents == [
        "Em đang giữ Thứ Tư 21/10, 9 giờ sáng.",
        "Còn 2 giờ chiều em chưa giữ ạ!",
    ]
    clauses = tokens.split_clauses("Thứ Tư 21/10, 9 giờ sáng, còn 2 giờ chiều thì sao",
                                   break_words=("còn",))
    assert clauses == ["Thứ Tư 21/10, 9 giờ sáng", "2 giờ chiều thì sao"]


def test_scan_spoken_clocks_and_scan_times():
    assert tokens.scan_spoken_clocks("Thứ Tư 21/10, 9 giờ sáng và 3 giờ rưỡi chiều") == [
        (9, 0, "sáng"),
        (3, 30, "chiều"),
    ]
    assert tokens.scan_times("dời qua 15h30 hoặc 3 giờ rưỡi chiều hay 16:00") == [
        (15, None, 30),
        (3, "chiều", 30),
        (16, None, 0),
    ]
    # "3 giờ chiều 2 người" không được lấy "2" làm phút
    assert tokens.scan_times("ừ 3 giờ chiều 2 người") == [(3, "chiều", None)]


def test_scan_day_offsets_weekdays_and_anchor_helpers():
    assert tokens.scan_day_offsets("chiều mai hoặc ngày mốt") == [1, 2]
    assert tokens.scan_day_offsets("một giờ chiều") == []
    assert tokens.scan_weekdays("thứ năm hoặc chủ nhật") == [3, 6]
    assert tokens.scan_weekdays("thu 6 hay cn") == [4, 6]
    assert tokens.names_a_clock("dời qua 4 giờ chiều") is True
    assert tokens.names_a_day("dời qua 4 giờ chiều") is False
    assert tokens.names_a_day("4 giờ chiều ngày 10/8") is True
    assert tokens.names_a_clock("giờ em qua được không") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_tokens.py`
Expected: FAIL with `ImportError: cannot import name 'tokens' from 'app.agents.booking_graph'`

- [ ] **Step 3: Write minimal implementation (`app/agents/booking_graph/tokens.py`)**

```python
"""Xử lý từ, câu, mệnh đề và mốc giờ tiếng Việt bằng Python thuần — 0 regex.

Mọi phép so khớp từ biên `(?<!\\w)...(?!\\w)` được thay bằng tìm chuỗi con
`f" {phrase} "` trên chuỗi đã chuẩn hoá khoảng trắng `padded(text)`.
"""
from typing import Iterable, List, Optional, Sequence, Tuple

from app.core.text import strip_diacritics

_SENTENCE_PUNCT = frozenset(".!?…")
_CLAUSE_PUNCT = frozenset(";.!?…\n")

_DAY_OFFSET_PHRASES: Tuple[Tuple[str, int], ...] = (
    ("hôm nay", 0),
    ("ngày mai", 1),
    ("ngày kia", 2),
    ("mai", 1),
    ("mốt", 2),
)
_DAY_OFFSET_BARE_PHRASES: Tuple[Tuple[str, int], ...] = (
    ("hom nay", 0),
    ("ngay mai", 1),
    ("ngay kia", 2),
    ("mai", 1),
    ("mot", 2),
)

_WEEKDAY_MAP = {
    "hai": 0, "ba": 1, "tư": 2, "năm": 3, "sáu": 4, "bảy": 5,
    "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5,
}
_WEEKDAY_BARE_MAP = {strip_diacritics(k): v for k, v in _WEEKDAY_MAP.items()}

_PERIOD_MAP = {
    "sáng": "sáng", "trưa": "trưa", "chiều": "chiều", "tối": "tối",
    "sang": "sáng", "trua": "trưa", "chieu": "chiều", "toi": "tối",
}
_SPOKEN_PERIODS = frozenset({"sáng", "trưa", "chiều", "tối", "đêm"})

_DAY_MARKER_WORDS = frozenset({
    "nay", "mai", "kia", "hom", "bua", "thu", "cn", "tuan", "thang", "ngay",
})


def _tokenize_with_pipes(text: str, *, lower: bool = True,
                         mask_accented: bool = False) -> List[str]:
    """Tách từ và giữ vách ngăn `|` tại dấu câu để cụm từ không vượt qua dấu phẩy/chấm."""
    if not text:
        return []
    out: List[str] = []
    cur: List[str] = []
    saw_punct = False
    for ch in text:
        if ch.isalnum() and ch != "_":
            if saw_punct and out and out[-1] != "|":
                out.append("|")
            saw_punct = False
            cur.append(ch.lower() if lower else ch)
        else:
            if cur:
                tok = "".join(cur)
                if mask_accented and strip_diacritics(tok) != tok:
                    tok = "_"
                out.append(tok)
                cur = []
            if not ch.isspace():
                saw_punct = True
    if cur:
        if saw_punct and out and out[-1] != "|":
            out.append("|")
        tok = "".join(cur)
        if mask_accented and strip_diacritics(tok) != tok:
            tok = "_"
        out.append(tok)
    return out


def cased_words(text: str) -> List[str]:
    """Tách từ giữ nguyên chữ hoa/thường; bỏ dấu câu và ký tự `_`."""
    if not text:
        return []
    cleaned = "".join(ch if (ch.isalnum() and ch != "_") else " " for ch in text)
    return cleaned.split()


def words(text: str) -> List[str]:
    """Tách từ chữ thường; bỏ dấu câu và ký tự `_`."""
    return [w.lower() for w in cased_words(text)]


def unaccented_words(text: str) -> List[str]:
    """Giữ các từ khách gõ không dấu; từ có dấu thay bằng `"_"`."""
    return [w if strip_diacritics(w) == w else "_" for w in words(text)]


def padded(text: str) -> str:
    """Chuỗi từ chữ thường có khoảng trắng đệm hai đầu và `|` tại ranh giới dấu câu."""
    ws = _tokenize_with_pipes(text, lower=True, mask_accented=False)
    return f" {' '.join(ws)} " if ws else " "


def padded_bare(text: str) -> str:
    """Chuỗi từ đã bỏ dấu hoàn toàn, có khoảng trắng đệm hai đầu và `|` tại dấu câu."""
    return strip_diacritics(padded(text))


def padded_unaccented_only(text: str) -> str:
    """Chuỗi từ trong đó chữ có dấu đã bị che thành `'_'`, có khoảng trắng đệm hai đầu."""
    ws = _tokenize_with_pipes(text, lower=True, mask_accented=True)
    return f" {' '.join(ws)} " if ws else " "


def has_phrase(padded_text: str, phrases: Iterable[str]) -> bool:
    """Kiểm tra có từ/cụm từ nào trong `phrases` xuất hiện trọn vẹn ở biên từ."""
    return any(f" {p} " in padded_text for p in phrases if p)


def digits_in(text: str) -> List[str]:
    """Mọi cụm chữ số ASCII (`0-9`) liên tiếp trong `text`."""
    if not text:
        return []
    only_digits = "".join(ch if ch.isascii() and ch.isdigit() else " " for ch in text)
    return only_digits.split()


def split_sentences(text: str) -> List[str]:
    """Tách câu tại dấu `.`, `!`, `?`, `…` khi theo sau là khoảng trắng hoặc cuối chuỗi."""
    s = (text or "").strip()
    if not s:
        return []
    out: List[str] = []
    start = 0
    n = len(s)
    i = 0
    while i < n:
        if s[i] in _SENTENCE_PUNCT and (i + 1 == n or s[i + 1].isspace()):
            piece = s[start:i + 1].strip()
            if piece:
                out.append(piece)
            i += 1
            while i < n and s[i].isspace():
                i += 1
            start = i
            continue
        i += 1
    if start < n:
        tail = s[start:].strip()
        if tail:
            out.append(tail)
    return out


def _comma_before_digit(text: str, idx: int) -> bool:
    """Dấu phẩy tại `idx` đứng trước chữ số (vd `'Thứ Tư 21/10, 9 giờ sáng'`)."""
    j = idx + 1
    n = len(text)
    while j < n and text[j] in (" ", "\t"):
        j += 1
    return j < n and text[j].isdigit()


def split_clauses(text: str, *, break_words: Sequence[str] = (),
                  preserve_date_comma: bool = True) -> List[str]:
    """Tách mệnh đề theo dấu câu và các từ nối `break_words` (không dùng regex)."""
    s = text or ""
    if not s.strip():
        return []
    buf: List[str] = []
    chunks: List[str] = []
    for idx, ch in enumerate(s):
        if ch in _CLAUSE_PUNCT or (ch == "," and not (preserve_date_comma and _comma_before_digit(s, idx))):
            piece = "".join(buf).strip()
            if piece:
                chunks.append(piece)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        chunks.append(tail)

    if not break_words:
        return chunks

    lowered_breaks = {bw.lower() for bw in break_words}
    final: List[str] = []
    for chunk in chunks:
        # Tách thêm tại từ nối đứng độc lập (cách bởi khoảng trắng)
        tokens_raw = chunk.split()
        cur: List[str] = []
        for tok in tokens_raw:
            core = "".join(c for c in tok if c.isalnum()).lower()
            if core in lowered_breaks:
                part = " ".join(cur).strip()
                if part:
                    final.append(part)
                cur = []
            else:
                cur.append(tok)
        rest = " ".join(cur).strip()
        if rest:
            final.append(rest)
    return final


def scan_spoken_clocks(text: str) -> List[Tuple[int, int, Optional[str]]]:
    """Tìm mọi giờ đọc kiểu `'9 giờ sáng'`, `'3 giờ rưỡi chiều'`, `'3 giờ 15'`."""
    ws = _tokenize_with_pipes(text, lower=True)
    out: List[Tuple[int, int, Optional[str]]] = []
    n = len(ws)
    i = 0
    while i < n - 1:
        tok = ws[i]
        if tok.isascii() and tok.isdigit() and 1 <= len(tok) <= 2 and ws[i + 1] == "giờ":
            hour = int(tok)
            minute = 0
            j = i + 2
            if j < n:
                if ws[j] == "rưỡi":
                    minute = 30
                    j += 1
                elif ws[j].isascii() and ws[j].isdigit() and len(ws[j]) == 2:
                    minute = int(ws[j])
                    j += 1
            period: Optional[str] = None
            if j < n and ws[j] in _SPOKEN_PERIODS:
                period = ws[j]
                j += 1
            out.append((hour, minute, period))
            i = j
            continue
        i += 1
    return out


def _split_attached_unit(tok: str) -> Optional[Tuple[int, str, Optional[int]]]:
    """Tách token dính kiểu `'15h'`, `'15h30'`, `'9g'`, `'3giờ'`."""
    for unit in ("giờ", "gio", "h", "g"):
        idx = tok.find(unit)
        if idx > 0:
            left, right = tok[:idx], tok[idx + len(unit):]
            if left.isascii() and left.isdigit() and 1 <= len(left) <= 2:
                if right == "":
                    return int(left), unit, None
                if right in ("rưỡi", "ruoi"):
                    return int(left), unit, 30
                if right.isascii() and right.isdigit() and len(right) == 2:
                    return int(left), unit, int(right)
    return None


def scan_times(text: str) -> List[Tuple[int, Optional[str], Optional[int]]]:
    """Tìm mọi mốc `(hour, period, minute)` trong câu khách (có dấu hoặc không dấu)."""
    if not text:
        return []
    out: List[Tuple[int, Optional[str], Optional[int]]] = []
    ws = _tokenize_with_pipes(text, lower=True)
    n = len(ws)
    i = 0
    while i < n:
        tok = ws[i]
        attached = _split_attached_unit(tok)
        if attached is not None:
            hour, _, minute = attached
            j = i + 1
            if minute is None and j < n:
                if ws[j] in ("rưỡi", "ruoi"):
                    minute = 30
                    j += 1
                elif ws[j].isascii() and ws[j].isdigit() and len(ws[j]) == 2:
                    minute = int(ws[j])
                    j += 1
            period = _PERIOD_MAP.get(ws[j]) if j < n else None
            if period is not None:
                j += 1
                if minute is None and j < n:
                    if ws[j] in ("rưỡi", "ruoi"):
                        minute = 30
                        j += 1
                    elif ws[j].isascii() and ws[j].isdigit() and len(ws[j]) == 2:
                        minute = int(ws[j])
                        j += 1
            out.append((hour, period, minute))
            i = j
            continue

        if tok.isascii() and tok.isdigit() and 1 <= len(tok) <= 2 and i + 1 < n:
            nxt = ws[i + 1]
            if nxt in ("giờ", "gio", "h", "g"):
                hour = int(tok)
                j = i + 2
                period = None
                minute = None
                if j < n and ws[j] in _PERIOD_MAP:
                    period = _PERIOD_MAP[ws[j]]
                    j += 1
                if j < n:
                    if ws[j] in ("rưỡi", "ruoi"):
                        minute = 30
                        j += 1
                    elif ws[j].isascii() and ws[j].isdigit() and len(ws[j]) == 2:
                        minute = int(ws[j])
                        j += 1
                if period is None and j < n and ws[j] in _PERIOD_MAP:
                    period = _PERIOD_MAP[ws[j]]
                    j += 1
                out.append((hour, period, minute))
                i = j
                continue
            if nxt in ("rưỡi", "ruoi"):
                out.append((int(tok), None, 30))
                i += 2
                continue
        i += 1

    # Dạng HH:MM viết bằng dấu hai chấm (vd "15:30", "15:00")
    for raw in (text or "").split():
        cleaned = raw.strip(".,!?;:()[]\"'«»")
        if ":" in cleaned:
            left, sep, right = cleaned.partition(":")
            if (sep and left.isascii() and right.isascii()
                    and left.isdigit() and right.isdigit()
                    and 1 <= len(left) <= 2 and len(right) == 2):
                out.append((int(left), None, int(right)))
    return out


def scan_day_offsets(text: str) -> List[int]:
    """Các offset ngày tương đối (`0`=hôm nay, `1`=mai, `2`=ngày kia/mốt)."""
    if not text:
        return []
    lowered = text.lower()
    bare = strip_diacritics(lowered)
    typed_bare = bare == lowered
    p_acc = padded(lowered)
    p_bare = padded(bare)
    out: List[int] = []
    # Ưu tiên cụm 2 từ ("ngày mai") trước từ đơn ("mai")
    work_acc = p_acc
    for phrase, offset in _DAY_OFFSET_PHRASES:
        target = f" {phrase} "
        while target in work_acc:
            out.append(offset)
            work_acc = work_acc.replace(target, " ", 1)
    work_bare = p_bare
    for phrase, offset in _DAY_OFFSET_BARE_PHRASES:
        if phrase == "mot" and not typed_bare:
            continue
        target = f" {phrase} "
        while target in work_bare:
            if offset not in out:
                out.append(offset)
            work_bare = work_bare.replace(target, " ", 1)
    return out


def scan_weekdays(text: str) -> List[int]:
    """Các thứ trong tuần khách nhắc (`0`=Thứ Hai .. `6`=Chủ Nhật)."""
    ws = words(text)
    ws_bare = [strip_diacritics(w) for w in ws]
    out: List[int] = []
    n = len(ws)
    for i in range(n):
        if ws[i] == "thứ" and i + 1 < n and ws[i + 1] in _WEEKDAY_MAP:
            out.append(_WEEKDAY_MAP[ws[i + 1]])
        elif ws_bare[i] == "thu" and i + 1 < n and ws_bare[i + 1] in _WEEKDAY_BARE_MAP:
            val = _WEEKDAY_BARE_MAP[ws_bare[i + 1]]
            if val not in out:
                out.append(val)
        elif ws[i] == "cn" or (ws[i] == "chủ" and i + 1 < n and ws[i + 1] == "nhật"):
            out.append(6)
        elif ws_bare[i] == "chu" and i + 1 < n and ws_bare[i + 1] == "nhat":
            if 6 not in out:
                out.append(6)
    return out


def has_next_week(text: str) -> bool:
    """Câu khách có nhắc `'tuần sau'` hoặc `'tuần tới'` (có dấu hoặc không dấu)."""
    pb = padded_bare(text)
    return has_phrase(pb, ("tuan sau", "tuan toi"))


def has_slash_date(text: str) -> bool:
    """Câu có chứa ngày/tháng kiểu `10/8` hoặc `21/10`."""
    for raw in (text or "").split():
        cleaned = raw.strip(".,!?;:()[]\"'«»")
        if "/" in cleaned:
            parts = cleaned.split("/")
            if (len(parts) >= 2
                    and all(p.isascii() and p.isdigit() and 1 <= len(p) <= 2 for p in parts[:2])):
                return True
    return False


def names_a_day(text: str) -> bool:
    """Câu có nêu một từ chỉ ngày/thứ/tuần/tháng hoặc `dd/mm`."""
    if not text:
        return False
    if has_slash_date(text):
        return True
    ws_bare = [strip_diacritics(w) for w in words(text)]
    for i, w in enumerate(ws_bare):
        if w in _DAY_MARKER_WORDS:
            return True
        if w == "mot":
            # "mot" đứng ngay trước "gio"/"h"/"g" là "1 giờ", vẫn tính như regex cũ
            return True
        if w == "chu" and i + 1 < len(ws_bare) and ws_bare[i + 1] == "nhat":
            return True
    return False


def names_a_clock(text: str) -> bool:
    """Câu có nêu một mốc giờ bằng số (`'4 giờ'`, `'16h'`, `'15:30'`, `'3 rưỡi'`)."""
    return bool(scan_times(text))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_tokens.py`
Expected: PASS (`7 passed`)

- [ ] **Step 5: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/tokens.py tests/test_tokens.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "feat: add regex-free tokens and Vietnamese time scanner module

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Mở rộng `TurnAnalysis` (`aside`, `mentioned_times`) trong `analyzer.py` và `prompts.py`

**Files:**
- Modify: `app/agents/booking_graph/analyzer.py:34-152`
- Modify: `app/agents/booking_graph/prompts.py:502-545`
- Test: `tests/test_analyzer.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `tokens.py` (từ Task 1).
- Produces:
  - `AsideKind = Literal["relative_free", "defer_only", "defer_with_request"]`
  - `PartOfDay = Literal["sang", "trua", "chieu", "toi"]`
  - `class MentionedTime(BaseModel)`: `hour: Optional[int] = None`, `minute: Optional[int] = None`, `part_of_day: Optional[PartOfDay] = None`, `weekday: Optional[int] = None`, `day: Optional[int] = None`, `month: Optional[int] = None`, `day_offset: Optional[int] = None`.
  - `TurnAnalysis` có thêm `aside: Optional[AsideKind] = None` và `mentioned_times: List[MentionedTime] = Field(default_factory=list)`.
  - `sanitize_analysis` lọc `aside` (chỉ giữ 1 trong 3 giá trị hợp lệ) và lọc `mentioned_times` (bỏ mục có `hour` ngoài `0..23`, `minute` ngoài `0..59`, `weekday` ngoài `0..6`, `day` ngoài `1..31`, `month` ngoài `1..12`).

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `tests/test_analyzer.py`:

```python
def test_turn_analysis_sanitizes_aside_and_mentioned_times():
    from app.agents.booking_graph.analyzer import MentionedTime, TurnAnalysis, sanitize_analysis

    raw = TurnAnalysis(
        route="booking",
        aside="defer_only",
        mentioned_times=[
            MentionedTime(hour=15, minute=30, part_of_day="chieu", weekday=3, day_offset=1),
            MentionedTime(hour=99, minute=70),  # ngoài khoảng hợp lệ -> bị loại
        ],
    )
    cleaned = sanitize_analysis(raw, "để chú hỏi lại, mai 3h30 chiều", has_pending=False)
    assert cleaned.aside == "defer_only"
    assert len(cleaned.mentioned_times) == 1
    assert cleaned.mentioned_times[0].hour == 15
    assert cleaned.mentioned_times[0].minute == 30
```

Và cập nhật `TestAnalyzePrompt.test_analyze_prompt_mentions_every_output_field` trong `tests/test_prompts.py`:

```python
    def test_analyze_prompt_mentions_every_output_field(self):
        from app.agents.booking_graph.prompts import ANALYZE_PROMPT
        for field in ("route", "reply_to_proposal", "aside", "mentioned_times",
                     "service", "time_text", "self_address", "stated", "answers_question"):
            assert f"{field}:" in ANALYZE_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_analyzer.py tests/test_prompts.py`
Expected: FAIL (`ImportError: cannot import name 'MentionedTime'` và `AssertionError: assert 'aside:' in ANALYZE_PROMPT`)

- [ ] **Step 3: Write minimal implementation**

Trong `app/agents/booking_graph/analyzer.py`:

```python
Route = Literal["booking", "shop", "social"]
ReplyLabel = Literal["approve", "reject", "change", "unclear"]
SelfAddress = Literal["anh", "chị"]
AsideKind = Literal["relative_free", "defer_only", "defer_with_request"]
PartOfDay = Literal["sang", "trua", "chieu", "toi"]
_VALID_ASIDES = frozenset({"relative_free", "defer_only", "defer_with_request"})
_VALID_PARTS = frozenset({"sang", "trua", "chieu", "toi"})


class MentionedTime(BaseModel):
    """Một mốc thời gian có cấu trúc khách nhắc tới trong tin nhắn hiện tại."""

    hour: Optional[int] = None
    minute: Optional[int] = None
    part_of_day: Optional[PartOfDay] = None
    weekday: Optional[int] = None
    day: Optional[int] = None
    month: Optional[int] = None
    day_offset: Optional[int] = None


class TurnAnalysis(BaseModel):
    """Kết quả bộ phân tích. Mọi trường trừ `route` đều tuỳ chọn/rỗng —
    "không nói gì" là câu trả lời đúng cho phần lớn lượt tán gẫu."""

    route: Route = "booking"
    reply_to_proposal: Optional[ReplyLabel] = None
    aside: Optional[AsideKind] = None
    mentioned_times: List[MentionedTime] = Field(default_factory=list)
    service: Optional[str] = None
    time_text: Optional[str] = None
    self_address: Optional[SelfAddress] = None
    stated: List[str] = Field(default_factory=list)
    answers_question: bool = False
```

Thêm hàm `_clean_mentioned_time(item: MentionedTime) -> Optional[MentionedTime]` vào `app/agents/booking_graph/analyzer.py` và gọi trong `sanitize_analysis`:

```python
def _clean_mentioned_time(item: MentionedTime) -> Optional[MentionedTime]:
    if not isinstance(item, MentionedTime):
        return None
    hour = item.hour if (item.hour is not None and 0 <= item.hour <= 23) else None
    minute = item.minute if (item.minute is not None and 0 <= item.minute <= 59) else None
    part = item.part_of_day if item.part_of_day in _VALID_PARTS else None
    weekday = item.weekday if (item.weekday is not None and 0 <= item.weekday <= 6) else None
    day = item.day if (item.day is not None and 1 <= item.day <= 31) else None
    month = item.month if (item.month is not None and 1 <= item.month <= 12) else None
    offset = item.day_offset if (item.day_offset is not None and -1 <= item.day_offset <= 90) else None
    if all(v is None for v in (hour, minute, part, weekday, day, month, offset)):
        return None
    return MentionedTime(hour=hour, minute=minute, part_of_day=part,
                         weekday=weekday, day=day, month=month, day_offset=offset)
```

Trong `sanitize_analysis`:
```python
    aside = a.aside if a.aside in _VALID_ASIDES else None
    mentioned_times = [
        cleaned for item in (a.mentioned_times or [])[:4]
        if (cleaned := _clean_mentioned_time(item)) is not None
    ]
    return TurnAnalysis(
        route=a.route if a.route in ("booking", "shop", "social") else "booking",
        reply_to_proposal=reply_to_proposal,
        aside=aside,
        mentioned_times=mentioned_times,
        service=service,
        time_text=time_text,
        self_address=self_address,
        stated=stated,
        answers_question=bool(a.answers_question),
    )
```

Trong `app/agents/booking_graph/prompts.py`, thêm mô tả 2 trường `aside` và `mentioned_times` vào `ANALYZE_PROMPT` (100% ASCII, không dấu tiếng Việt):

```python
- aside: one of "relative_free", "defer_only", "defer_with_request", or leave
  it empty. "relative_free" when the customer wonders whether a relative or
  companion is free (never the salon owner). "defer_only" when they say they
  will ask someone, check their schedule, or message back later without asking
  to hold or change a slot right now. "defer_with_request" when they mention
  asking someone or deciding later AND also ask for a slot, a change, a
  cancellation or salon info in the same message.
- mentioned_times: every clock time, part of day ("sang", "trua", "chieu",
  "toi"), weekday (0 = Monday .. 6 = Sunday), calendar day/month, or relative
  day offset (0 = today, 1 = tomorrow, 2 = day after tomorrow, -1 = another
  unspecified day or next week) named in THIS message. Leave it empty when no
  time or day is mentioned.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_analyzer.py tests/test_prompts.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/analyzer.py app/agents/booking_graph/prompts.py tests/test_analyzer.py tests/test_prompts.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "feat: add aside and mentioned_times to TurnAnalysis

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Xoá toàn bộ 6 regex trong `relatives.py` (6 → 0 regex)

**Files:**
- Modify: `app/agents/booking_graph/relatives.py:1-109`
- Modify: `app/agents/booking_graph/agents.py:173-195`
- Test: `tests/test_relatives.py`

**Interfaces:**
- Consumes: `tokens.words`, `tokens.unaccented_words`, `tokens.padded`, `tokens.padded_bare`, `tokens.padded_unaccented_only`, `tokens.has_phrase`, `tokens.names_a_clock` (từ Task 1); `TurnAnalysis.aside` (từ Task 2).
- Produces:
  - `asks_if_relative_free(text: str) -> bool` (0 regex)
  - `defers(text: str) -> bool` (0 regex)
  - `has_request(text: str) -> bool` (0 regex)
  - `aside_line(text: str, analysis: Optional[dict] = None) -> str` (0 regex)
  - `deferral_reply(text: str, address: str, previous_replies, analysis: Optional[dict] = None) -> Optional[str]` (0 regex)

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `tests/test_relatives.py`:

```python
def test_relatives_module_uses_zero_regex():
    import inspect
    from app.agents.booking_graph import relatives
    source = inspect.getsource(relatives)
    assert "import re" not in source and "re.compile" not in source


def test_aside_line_and_deferral_reply_honour_analyzer_aside():
    assert "[relative]" in aside_line("câu bất kỳ", analysis={"aside": "relative_free"})
    assert "[deferral]" in aside_line("câu bất kỳ", analysis={"aside": "defer_only"})
    reply = deferral_reply("câu bất kỳ", "anh Hùng", [], analysis={"aside": "defer_only"})
    assert reply and "anh Hùng" in reply
    assert deferral_reply("câu bất kỳ", "anh Hùng", [], analysis={"aside": "defer_with_request"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_relatives.py`
Expected: FAIL (`AssertionError: assert 'import re' not in source`)

- [ ] **Step 3: Write minimal implementation**

Thay thế nội dung `app/agents/booking_graph/relatives.py` bằng phiên bản thuần `tokens.py` (0 regex):

```python
"""Khách nhắc NGƯỜI NHÀ / người quen — lỗi live B3 #2, B4 #2 (0 regex).

Nhận diện qua `TurnAnalysis.aside` (nếu đã có bản phân tích đầu lượt) kết hợp
kiểm tra từ/cụm từ thuần Python qua `tokens.py` cho lối tắt trước LLM ở
`supervisor.py` và `make_agent_node`.
"""
from typing import Any, Mapping, Optional, Sequence

from app.agents.booking_graph import tokens
from app.agents.booking_graph.guard import repeats

_PERSONS_ACC = (
    "nó", "thằng", "cháu", "con", "ông xã", "bà xã", "ông nhà", "bà nhà", "vợ", "chồng", "bố", "mẹ",
)
# Không dấu: bỏ "con" ("con ranh" = "còn rảnh") và "vo" ("vô").
_PERSONS_BARE = (
    "no", "thang", "chau", "ong xa", "ba xa", "ong nha", "ba nha", "chong",
)
_SHOP_WORDS = frozenset({"chủ", "chu", "tiệm", "tiem"})

_DEFER_VERBS_ACC = frozenset({"hỏi", "xem", "gọi", "tính", "nghĩ", "bàn"})
_DEFER_VERBS_BARE = frozenset({"hoi", "xem", "goi", "tinh", "nghi", "ban"})
_DEFER_PHRASES_ACC = ("xem lại", "hỏi lại", "suy nghĩ", "tính sau")
_DEFER_PHRASES_BARE = ("xem lai", "hoi lai", "suy nghi", "tinh sau")

_REQUEST_PHRASES_ACC = (
    "đặt", "giữ", "dời", "đổi", "hủy", "huỷ", "book", "mấy giờ", "giờ nào",
    "đóng cửa", "mở cửa", "giá", "bao nhiêu", "bao lâu", "còn chỗ", "còn trống",
)
_REQUEST_VERBS_BARE = frozenset({"dat", "giu", "doi", "huy"})
_REQUEST_OBJS_BARE = frozenset({
    "lich", "cho", "hen", "luon", "giup", "gium", "dum", "lai", "qua", "sang", "truoc", "di", "cai",
})
_REQUEST_PHRASES_BARE = (
    "book", "may gio", "gio nao", "dong cua", "mo cua", "bao nhieu", "bao lau", "con cho", "con trong",
)

RELATIVE_LINE = ("Khách vừa hỏi NGƯỜI NHÀ/NGƯỜI QUEN của khách có rảnh không — không phải "
                 "hỏi chủ tiệm bận hay rảnh, không phải hỏi lịch của khách khác. [relative]")
DEFERRAL_LINE = ("Khách nói sẽ hỏi lại/tính lại rồi nhắn em sau — đây là chuyện lịch của "
                 "chính khách; chỉ đáp một câu ngắn, mời khách nhắn lại. [deferral]")

_DEFERRAL_REPLIES = (
    "Dạ vâng, {address} cứ thong thả, xong thì nhắn em nhé.",
    "Dạ, khi nào {address} sẵn sàng thì nhắn lại em là được ạ.",
    "Dạ không sao ạ, có gì {address} cứ nhắn em nhé.",
)


def _person_near_free(word_list: Sequence[str], persons: Sequence[str], free_word: str) -> bool:
    """Tìm từ chỉ người trong `persons` đứng trước `free_word` trong phạm vi ≤ 3 từ giữa,
    mà khoảng giữa không vượt dấu câu (`|`) và không chứa từ chỉ chủ tiệm (`chủ`, `tiệm`)."""
    n = len(word_list)
    for phrase in persons:
        p_tokens = phrase.split()
        plen = len(p_tokens)
        for i in range(n - plen):
            if list(word_list[i:i + plen]) != p_tokens:
                continue
            start = i + plen
            for j in range(start, min(n, start + 4)):
                if word_list[j] == "|":
                    break
                if word_list[j] == free_word:
                    between = word_list[i:j + 1]
                    if not any(w in _SHOP_WORDS for w in between):
                        return True
    return False


def asks_if_relative_free(text: str) -> bool:
    """Khách hỏi một người KHÁCH nhắc tới có rảnh không (không phải chủ tiệm)."""
    ws = tokens.padded(text).split()
    if _person_near_free(ws, _PERSONS_ACC, "rảnh"):
        return True
    ws_unacc = tokens.padded_unaccented_only(text).split()
    return _person_near_free(ws_unacc, _PERSONS_BARE, "ranh")


def _has_de_verb(word_list: Sequence[str], de_word: str, verbs: frozenset) -> bool:
    """Khớp `'để <1..2 từ> (hỏi|xem|gọi|tính|nghĩ|bàn)'` hoặc `'nhắn [lại] [<1 từ>] sau'`."""
    n = len(word_list)
    for i, w in enumerate(word_list):
        if w == de_word:
            if i + 2 < n and word_list[i + 1] != "|" and word_list[i + 2] in verbs:
                return True
            if (i + 3 < n and "|" not in word_list[i + 1:i + 3]
                    and word_list[i + 3] in verbs):
                return True
        if w in ("nhắn", "nhan"):
            for step in (1, 2, 3):
                if (i + step < n and "|" not in word_list[i + 1:i + step]
                        and word_list[i + step] == "sau"):
                    return True
    return False


def defers(text: str) -> bool:
    """Khách sẽ hỏi ai đó / tính lại rồi nhắn sau."""
    if not text:
        return False
    p_acc = tokens.padded(text)
    p_bare = tokens.padded_bare(text)
    if tokens.has_phrase(p_acc, _DEFER_PHRASES_ACC) or tokens.has_phrase(p_bare, _DEFER_PHRASES_BARE):
        return True
    ws_acc = p_acc.split()
    ws_bare = p_bare.split()
    return (_has_de_verb(ws_acc, "để", _DEFER_VERBS_ACC)
            or _has_de_verb(ws_bare, "de", _DEFER_VERBS_BARE))


def has_request(text: str) -> bool:
    """Câu khách có kèm một yêu cầu cần model (giờ, đặt/giữ/dời/hủy, hỏi tiệm)."""
    if not text:
        return False
    p_acc = tokens.padded(text)
    if tokens.has_phrase(p_acc, _REQUEST_PHRASES_ACC):
        return True
    p_unacc = tokens.padded_unaccented_only(text)
    if tokens.has_phrase(p_unacc, _REQUEST_PHRASES_BARE):
        return True
    ws_unacc = tokens.unaccented_words(text)
    for i in range(len(ws_unacc) - 1):
        if ws_unacc[i] in _REQUEST_VERBS_BARE and ws_unacc[i + 1] in _REQUEST_OBJS_BARE:
            return True
    return tokens.names_a_clock(text)


def aside_line(text: str, analysis: Optional[Mapping[str, Any]] = None) -> str:
    """Dòng code viết cho khối bối cảnh; rỗng khi câu khách không thuộc hai ca."""
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    rel = asks_if_relative_free(text) or aside == "relative_free"
    dfd = defers(text) or aside in ("defer_only", "defer_with_request")
    lines = [line for hit, line in ((rel, RELATIVE_LINE), (dfd, DEFERRAL_LINE)) if hit]
    return "\n".join(lines)


def deferral_reply(text: str, address: str, previous_replies,
                   analysis: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """Câu đáp code viết khi khách chỉ hoãn; None khi không phải câu hoãn hoặc
    câu có kèm yêu cầu."""
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    if aside == "defer_with_request":
        return None
    is_plain_defer = (aside == "defer_only") or (defers(text) and not has_request(text))
    if not is_plain_defer or has_request(text):
        return None
    options = [r.format(address=address) for r in _DEFERRAL_REPLIES]
    previous = [p for p in previous_replies or [] if p]
    return next((o for o in options if o not in previous and not repeats(o, previous)), options[-1])
```

Trong `app/agents/booking_graph/agents.py:173-191`, truyền thêm `analysis=state.get("analysis")` vào `deferral_reply` và `aside_line`:

```python
            code = deferral_reply(state.get("customer_text") or "", address_phrase(user),
                                  [*said, *(state.get("previous_replies") or [])],
                                  analysis=state.get("analysis"))
```
và:
```python
            "context_block": with_line(with_line(state.get("context_block", ""),
                                                 queued_line(queue)),
                                       aside_line(state.get("customer_text") or "",
                                                  analysis=state.get("analysis"))),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_relatives.py tests/test_supervisor.py tests/test_confirm_flow.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/relatives.py app/agents/booking_graph/agents.py tests/test_relatives.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "refactor: remove all 6 regexes from relatives.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Xoá toàn bộ 14 regex trong `reply.py` (14 → 0 regex)

**Files:**
- Modify: `app/agents/booking_graph/reply.py:1-490`
- Modify: `app/agents/booking_graph/confirm.py:328-335`
- Test: `tests/test_reply_veto.py`, `tests/test_reply_classify.py`, `tests/test_confirm.py`

**Interfaces:**
- Consumes: `tokens.words`, `tokens.padded`, `tokens.padded_unaccented_only`, `tokens.has_phrase`, `tokens.split_clauses`, `tokens.scan_times`, `tokens.scan_day_offsets`, `tokens.scan_weekdays`, `tokens.has_next_week` (từ Task 1); `relatives.defers` (từ Task 3); `TurnAnalysis.mentioned_times`, `TurnAnalysis.aside` (từ Task 2).
- Produces: Giữ nguyên toàn bộ hàm public trong `reply.py` (`mentions_other_time`, `other_time_veto`, `veto_reason`, `is_short_affirmation`, `is_go_ahead`, `classify_reply`, `decide`, `_describe`, `strip_diacritics`) với **0 regex (`re.compile` = 0)**.

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `tests/test_reply_veto.py`:

```python
def test_reply_module_uses_zero_regex():
    import inspect
    from app.agents.booking_graph import reply
    source = inspect.getsource(reply)
    assert "import re" not in source and "re.compile" not in source


def test_mentions_other_time_also_checks_structured_mentioned_times():
    from app.agents.booking_graph.analyzer import MentionedTime
    # Kể cả khi text không chứa chữ số (vd viết bằng chữ), mentioned_times vẫn bắt được giờ lệch
    assert mentions_other_time(
        "dạ bốn giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=16, part_of_day="chieu")],
    ) is True
    assert mentions_other_time(
        "dạ ba giờ chiều nha", P_BOOK["start_at"], NOW,
        mentioned_times=[MentionedTime(hour=15, part_of_day="chieu")],
    ) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_reply_veto.py`
Expected: FAIL (`AssertionError: assert 'import re' not in source`)

- [ ] **Step 3: Write minimal implementation**

Trong `app/agents/booking_graph/reply.py`:
- Xoá `import re`, xoá `_DAY_OFFSETS, _TIME_PATTERN` khỏi dòng import `timeparse`, và xoá toàn bộ 14 `re.compile` (`_words`, `_NEG_BY_KIND`, `_DEFERRAL`, `_DEFERRAL_BARE`, `_ASKING`, `_ASKING_BARE`, `_NOT_ASKING`, `_unaccented_only`, `_TIME`, `_TIME_BARE`, `_MINUTE_AFTER`, `_COLON_TIME`, `_HALF_PAST`, `_DAY`, `_DAY_BARE`, `_WEEKDAY`, `_WEEKDAY_BARE`, `_NEXT_WEEK`, `_CLAUSE_SPLIT`).
- Thay phần đầu và các hàm kiểm tra phủ quyết trong `app/agents/booking_graph/reply.py` bằng:

```python
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
    local_start, local_now = to_local(start), to_local(now)
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
```

Và cập nhật `is_short_affirmation`, `is_go_ahead`, `other_time_veto`, `veto_reason`, `_has_time`, `_without_queued_clauses` trong `app/agents/booking_graph/reply.py`:

```python
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
```

Trong `app/agents/booking_graph/confirm.py:328-333`, bổ sung hậu kiểm trên `analysis.aside` và `analysis.mentioned_times` bên trong `label_fn`:

```python
        async def label_fn(proposal_facts, bot_question, customer_text):
            analysis = await analyzer.analyze_turn(
                [], customer_text, proposal_facts, bot_question=bot_question)
            captured["analysis"] = analysis.model_dump()
            if analysis.aside in ("defer_only", "defer_with_request"):
                return "reject"
            if (analysis.mentioned_times
                    and not proposal_facts.get("queued")
                    and reply.mentions_other_time("", proposal.start_at, now_utc(),
                                                  mentioned_times=analysis.mentioned_times)):
                return "change"
            return analysis.reply_to_proposal or "unclear"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_reply_veto.py tests/test_reply_classify.py tests/test_confirm.py tests/test_confirm_flow.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/reply.py app/agents/booking_graph/confirm.py tests/test_reply_veto.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "refactor: remove all 14 regexes from reply.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Xoá `_DATE_ONLY` trong `tools.py` (1 → 0) và `_NAMES_A_DAY`, `_NAMES_A_CLOCK` trong `timeparse.py` (3 → 1)

**Files:**
- Modify: `app/agents/booking_graph/tools.py:1-32, 190-203, 315-320`
- Modify: `app/agents/booking_graph/timeparse.py:51-71, 317-354`
- Test: `tests/test_timeparse.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `tokens.names_a_day`, `tokens.names_a_clock` (từ Task 1).
- Produces:
  - `tools.py`: `_is_date_only(value: Optional[str]) -> bool` (0 regex trong toàn `tools.py`).
  - `timeparse.py`: `ParsedTime` có thêm `has_explicit_day: bool = False`, `has_explicit_clock: bool = False`; `anchor_day_applies(text: str, candidate: Optional[ParsedTime] = None) -> bool` dùng `tokens.names_a_clock(text)` và `not tokens.names_a_day(text)`; chỉ còn đúng **1 regex** (`_FULL`) trong `timeparse.py`.

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `tests/test_timeparse.py`:

```python
def test_timeparse_only_compiles_the_single_fast_path_regex():
    import inspect, re
    from app.agents.booking_graph import timeparse
    source = inspect.getsource(timeparse)
    assert len(re.findall(r"re\.compile\(", source)) == 1
```

Và thêm vào cuối `tests/test_tools.py`:

```python
def test_tools_module_uses_zero_regex():
    import inspect
    from app.agents.booking_graph import tools
    source = inspect.getsource(tools)
    assert "import re" not in source and "re.compile" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_timeparse.py tests/test_tools.py`
Expected: FAIL (`assert 3 == 1` in `test_timeparse.py` và `assert 'import re' not in source` in `test_tools.py`)

- [ ] **Step 3: Write minimal implementation**

Trong `app/agents/booking_graph/tools.py`:
- Xoá `import re` và `_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")`.
- Thêm hàm `_is_date_only`:
```python
def _is_date_only(value: Optional[str]) -> bool:
    """Chuỗi chỉ chứa ngày `YYYY-MM-DD`, không có giờ."""
    s = (value or "").strip()
    parts = s.split("-")
    return (len(s) == 10 and len(parts) == 3
            and [len(p) for p in parts] == [4, 2, 2]
            and all(p.isascii() and p.isdigit() for p in parts))
```
- Thay 2 chỗ `_DATE_ONLY.match(...)` (ở `_queued_item` và `propose_appointment`) bằng `_is_date_only(...)`.

Trong `app/agents/booking_graph/timeparse.py`:
- Thêm `from app.agents.booking_graph import tokens` ở đầu file.
- Thêm 2 trường vào `ParsedTime`:
```python
    has_explicit_day: bool = Field(
        default=False, description="True khi câu khách tự nêu ngày, thứ, hôm nay/mai/mốt hoặc tuần"
    )
    has_explicit_clock: bool = Field(
        default=False, description="True khi câu khách nêu một mốc giờ đồng hồ bằng số"
    )
```
- Xoá `_NAMES_A_DAY` và `_NAMES_A_CLOCK`, cập nhật `anchor_day_applies` và `put_on_anchor_day`:
```python
def anchor_day_applies(text: str, candidate: Optional[ParsedTime] = None) -> bool:
    """Câu nêu một giờ cụ thể mà KHÔNG nêu ngày nào."""
    if candidate is not None and candidate.has_explicit_day:
        return False
    return tokens.names_a_clock(text or "") and not tokens.names_a_day(text or "")


def put_on_anchor_day(candidate: ParsedTime, anchor: Optional[datetime],
                      text: str) -> ParsedTime:
    start = candidate.start_at
    if anchor is None or start is None or not anchor_day_applies(text, candidate):
        return candidate
    local = start.astimezone(TZ) if start.tzinfo else start.replace(tzinfo=TZ)
    day = anchor.astimezone(TZ).date()
    if local.date() == day:
        return candidate
    moved = local.replace(year=day.year, month=day.month, day=day.day)
    logger.info("timeparse_anchor_day", extra={"llm": local.isoformat(),
                                                "moved": moved.isoformat()})
    return candidate.model_copy(update={"start_at": moved, "partial_date": day})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_timeparse.py tests/test_tools.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/tools.py app/agents/booking_graph/timeparse.py tests/test_timeparse.py tests/test_tools.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "refactor: drop _DATE_ONLY in tools.py and anchor regexes in timeparse.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Tinh giản `guard.py` từ 21 xuống 2 regex và thêm hàng rào `test_regex_budget.py`

**Files:**
- Modify: `app/agents/booking_graph/guard.py:1-576`
- Create: `tests/test_regex_budget.py`
- Test: `tests/test_guard.py`, `tests/test_mw_guard.py`, `tests/test_mw_phrase.py`, `tests/test_regex_budget.py`

**Interfaces:**
- Consumes: `tokens.words`, `tokens.cased_words`, `tokens.padded`, `tokens.has_phrase`, `tokens.digits_in`, `tokens.split_sentences`, `tokens.split_clauses`, `tokens.scan_spoken_clocks` (từ Task 1).
- Produces:
  - Giữ nguyên toàn bộ hàm public trong `guard.py` (`strip_tool_names`, `unclaim`, `clock_keys`, `claim_facts`, `false_claim`, `has_false_claim`, `drop_false_claims`, `announce_queue`, `normalize`, `sentences`, `repeats`, `customer_asked_to_repeat`, `check_pronoun`, `asks_yes_no_about_a_clock`, `check_clock`, `content_kept`, `find_violations`, `apply_guard`).
  - Trong `guard.py` chỉ còn đúng **2 `re.compile`**: `_TOOL_GROUP` và `_TOOL_BARE`.

- [ ] **Step 1: Write the failing test (`tests/test_regex_budget.py`)**

```python
"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`).

Spec `2026-10-27-regex-reduction-design.md`: cắt từ 45 xuống còn đúng 3 regex:
- `timeparse.py`: 1 (`_FULL` theo bẫy #11 CONTEXT.md)
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác trong `app/agents/booking_graph/`: 0 regex.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking_graph"

EXPECTED_BUDGET = {
    "timeparse.py": 1,
    "guard.py": 2,
}


def test_agent_regex_budget_is_at_most_three():
    actual = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        count = len(re.findall(r"re\.compile\(", path.read_text(encoding="utf-8")))
        if count:
            actual[rel] = count
    assert actual == EXPECTED_BUDGET, f"Vượt ngân sách regex tầng agent: {actual}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_regex_budget.py`
Expected: FAIL (`AssertionError: Vượt ngân sách regex tầng agent: {'guard.py': 21, 'timeparse.py': 1}`)

- [ ] **Step 3: Write minimal implementation (`app/agents/booking_graph/guard.py`)**

Trong `app/agents/booking_graph/guard.py`, thêm `from app.agents.booking_graph import tokens`, xoá toàn bộ 19 `re.compile` ngoài `_TOOL_GROUP` và `_TOOL_BARE` (`_PUNCT`, `_SENTENCE_END`, `_REGISTER_BEFORE_NAME`, `_CON_AS_SUBJECT`, `_CLOCK_COLON`, `_CLOCK_WORDS`, `_HONORIFIC_BEFORE`, `_MULTI_SPACE`, `_SPACE_BEFORE_PUNCT`, `_CLAIM`, `_RỒI_AFTER`, `_CLAIMED`, `_CLAIM_NEGATED`, `_CLAUSE_SPLIT`, `_SPOKEN_CLOCK`, `_QUESTION_AFTER_COMMA`, `_DATETIME`, `_NUMBER`, `_YES_NO_PARTICLE`), và thay các hàm tương ứng bằng code thuần Python sau:

```python
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple
from app.agents.booking_graph import tokens

_FORBIDDEN_REGISTER = {"Cô", "Chú", "Bác"}
_LEADING_PARTICLES = {"dạ", "vâng", "ừ", "à", "ờ"}
_CON_VERBS = ("xem", "giúp", "đặt", "hỏi", "kiểm", "giữ")
_CON_SUBJECT_PHRASES = tuple(
    f"{prefix} {verb}"
    for prefix in ("con", "để con", "giúp con")
    for verb in _CON_VERBS
)
_NUMBER_WORDS_SET = frozenset({
    "một", "hai", "ba", "bốn", "lăm", "năm", "sáu", "bảy", "tám", "chín", "mười", "mươi", "mốt",
})
_HONORIFICS_SET = frozenset({"anh", "chị", "em", "cô", "chú", "bác", "ông", "bà"})

TOOL_NAMES = ("parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
              "cancel_appointment", "get_shop_hours", "get_shop_status")
_TOOL_ALT = "|".join(TOOL_NAMES)
_SNAKE_IDENT = r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"
_TOOL_GROUP = re.compile(
    rf"[ \t]*[(\[][^()\[\]]*?\b(?:{_TOOL_ALT}|{_SNAKE_IDENT})\b[^()\[\]]*[)\]]")
_BARE_NAME = rf"`?\b(?:{_TOOL_ALT})\b(?:\(\))?`?"
_TOOL_BARE = re.compile(rf"{_BARE_NAME}(?:[ \t]*(?:,|và|and)[ \t]*{_BARE_NAME})*")

RewriteOnce = Callable[[str, List[str], Sequence[str], str], Awaitable[Optional[str]]]


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


def drop_false_claims(text: str, facts: dict) -> str:
    """Bỏ nguyên câu có mệnh đề nhận vơ — lưới cuối khi rewrite không sửa được."""
    kept = [s for s in tokens.split_sentences((text or "").strip())
            if not any(false_claim(c, facts) for c in _clauses(s))]
    out = " ".join(s for s in kept if s.strip()).strip()
    if out.startswith(("Dạ ", "Dạ, ")) or not out:
        return out
    return out[:1].upper() + out[1:]
```

Trong `announce_queue`, thay `_SENTENCE_END.split(text)` và `_QUESTION_AFTER_COMMA.split(question, maxsplit=1)` bằng:

```python
    text = answer.strip()
    body = [p for p in tokens.split_sentences(text) if p.strip()]
    question = None
    if body and body[-1].rstrip().endswith("?"):
        question = body.pop().strip()
        last_comma = question.rfind(",")
        if last_comma != -1:
            after = question[last_comma + 1:].lstrip()
            if after and not after[0].isdigit():
                body.append(question[:last_comma].rstrip() + ".")
                question = after[:1].upper() + after[1:]
```

Và thay `normalize`, `sentences`, `_digits`, `_leading_content_tokens`, `check_pronoun`, `asks_yes_no_about_a_clock`, `check_clock`, `content_kept` bằng:

```python
def normalize(text: str) -> str:
    return " ".join(tokens.words(text))


def sentences(text: str) -> List[str]:
    return [p.rstrip(".!?…").strip() for p in tokens.split_sentences(text)
            if p.rstrip(".!?…").strip()]


def _digits(text: str) -> set:
    return set(tokens.digits_in(text))


def _leading_content_tokens(sentence: str) -> List[str]:
    toks = tokens.cased_words(sentence)
    i = 0
    while i < len(toks) and toks[i].lower() in _LEADING_PARTICLES:
        i += 1
    return toks[i:]


def _has_register_before_name(draft: str) -> bool:
    cw = tokens.cased_words(draft)
    for i in range(len(cw) - 1):
        if cw[i].lower() in ("cô", "chú", "bác"):
            nxt = cw[i + 1]
            if len(nxt) >= 2 and nxt[0].isupper() and nxt[1:].islower():
                return True
    return False


def check_pronoun(draft: str, address: str, name: str) -> bool:
    p = tokens.padded(draft)
    if _has_register_before_name(draft) or tokens.has_phrase(p, _CON_SUBJECT_PHRASES):
        return True
    _, wrong = _pronouns(address)
    for sent in sentences(draft):
        toks = _leading_content_tokens(sent)
        if not toks:
            continue
        if toks[0] in _FORBIDDEN_REGISTER:
            return True
        first = toks[0].lower()
        nxt = toks[1].lower() if len(toks) > 1 else ""
        if first in wrong and nxt != "chủ":
            if first == "anh" and nxt == "chị":
                continue
            return True
    if not wrong:
        return False
    if name and any(f" {w} {name.lower()} " in p for w in wrong):
        return True
    return False


def asks_yes_no_about_a_clock(draft: str) -> bool:
    parts = tokens.split_sentences(draft)
    last = parts[-1] if parts else ""
    if not last.rstrip().endswith("?"):
        return False
    return (tokens.has_phrase(tokens.padded(last), ("được không", "nhé", "nha"))
            and bool(clock_keys(draft)))


def check_clock(draft: str) -> bool:
    for raw in (draft or "").split():
        cleaned = raw.strip(".,!?;:()[]\"'«»")
        if ":" in cleaned:
            left, sep, right = cleaned.partition(":")
            if (sep and left.isascii() and right.isascii()
                    and left.isdigit() and right.isdigit()
                    and 1 <= len(left) <= 2 and len(right) == 2):
                return True
    cw = tokens.cased_words(draft)
    n = len(cw)
    for i, w in enumerate(cw):
        if w.islower() and w in _NUMBER_WORDS_SET:
            if i + 1 < n and cw[i + 1].lower() in ("giờ", "tháng"):
                start = i
                while start > 0 and cw[start - 1].islower() and cw[start - 1] in _NUMBER_WORDS_SET:
                    start -= 1
                if start > 0 and cw[start - 1].lower() in _HONORIFICS_SET:
                    continue
                return True
            if i > 0 and cw[i - 1].lower() == "ngày":
                return True
    return False


def content_kept(original: str, rewritten: str) -> bool:
    """Bản viết lại phải còn mọi mốc ngày-giờ và mọi con số của bản gốc."""
    orig_clocks = clock_keys(original)
    rew_clocks = clock_keys(rewritten)
    if any(c not in rew_clocks for c in orig_clocks):
        return False
    rew_digits = set(tokens.digits_in(rewritten))
    return all(d in rew_digits for d in tokens.digits_in(original))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_regex_budget.py tests/test_guard.py tests/test_mw_guard.py tests/test_mw_phrase.py`
Expected: PASS

- [ ] **Step 5: Run full backend test suite to verify zero regressions**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS (toàn bộ ~1.665 tests xanh, 0 failed)

- [ ] **Step 6: Commit**

```bash
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b add app/agents/booking_graph/guard.py tests/test_regex_budget.py
git -C /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b commit -m "refactor: reduce guard.py regexes from 21 to 2 and add regex budget test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

