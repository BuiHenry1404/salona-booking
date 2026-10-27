r"""Xử lý từ, câu, mệnh đề và mốc giờ tiếng Việt bằng Python thuần — 0 regex.

Mọi phép so khớp từ biên `(?<!\w)...(?!\w)` được thay bằng tìm chuỗi con
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
            # "mot" (dù là "mốt" hay "một" trong "một giờ") luôn được tính là có nhắc ngày để tương thích với regex cũ
            return True
        if w == "chu" and i + 1 < len(ws_bare) and ws_bare[i + 1] == "nhat":
            return True
    return False


def names_a_clock(text: str) -> bool:
    """Câu có nêu một mốc giờ bằng số (`'4 giờ'`, `'16h'`, `'15:30'`, `'3 rưỡi'`)."""
    return bool(scan_times(text))
