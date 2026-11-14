"""Bộ đọc từ, câu, mệnh đề, mốc giờ và số tiền tiếng Việt bằng Python thuần — 0 regex.

Chép lại từ `tokens.py` của gói agent cũ (đã xoá ở Task 16; chỉ 8 hàm quét dữ
kiện cần cho gói `salon` mới, kèm các hàm phụ riêng tư chúng cần) và bổ sung
`scan_money`. Phần còn lại của `tokens.py` (`has_next_week`, `has_slash_date`, `names_a_day`,
`names_a_clock`, `padded*`, `has_phrase`, `unaccented_words`, `cased_words`)
phục vụ resolver của pipeline cũ, không mang sang.
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

# Đơn vị tiền — là đơn vị đo, không phải từ khoá ý định (Global Constraints).
_MONEY_UNITS = {
    "k": 1_000,
    "nghìn": 1_000,
    "ngàn": 1_000,
    "đ": 1,
    "đồng": 1,
    "triệu": 1_000_000,
    "tr": 1_000_000,
}
_RANGE_WORDS = frozenset({"tới", "đến"})


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


def words(text: str) -> List[str]:
    """Tách từ chữ thường; bỏ dấu câu và ký tự `_`."""
    if not text:
        return []
    cleaned = "".join(ch if (ch.isalnum() and ch != "_") else " " for ch in text)
    return [w.lower() for w in cleaned.split()]


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


def _inside_number(text: str, idx: int) -> bool:
    """Dấu `.`/`,` tại `idx` kẹp giữa hai chữ số là dấu ngăn hàng nghìn / thập
    phân (`'350.000đ'`, `'1,5'`), không phải dấu ngắt mệnh đề."""
    return (text[idx] in ".," and 0 < idx < len(text) - 1
            and text[idx - 1].isdigit() and text[idx + 1].isdigit())


def split_clauses(text: str, *, break_words: Sequence[str] = (),
                  preserve_date_comma: bool = True) -> List[str]:
    """Tách mệnh đề theo dấu câu và các từ nối `break_words` (không dùng regex)."""
    s = text or ""
    if not s.strip():
        return []
    buf: List[str] = []
    chunks: List[str] = []
    for idx, ch in enumerate(s):
        if _inside_number(s, idx):
            buf.append(ch)
        elif ch in _CLAUSE_PUNCT or (ch == "," and not (preserve_date_comma and _comma_before_digit(s, idx))):
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
    ws_acc = _tokenize_with_pipes(lowered, lower=True)
    p_acc = f" {' '.join(ws_acc)} " if ws_acc else " "
    ws_bare = _tokenize_with_pipes(bare, lower=True)
    p_bare = f" {' '.join(ws_bare)} " if ws_bare else " "
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


def _tokenize_money(text: str) -> List[Tuple[str, object, object]]:
    """Tách `text` thành số (gộp dấu `.`/`,` ngăn hàng nghìn), chữ (thường) và dấu `-`.

    Một số dạng `("num", value, frac)`: `value` là các nhóm chữ số gộp lại
    (như trước). `frac` là `None`, trừ khi dấu phân cách cuối cùng là `,` và
    nhóm chữ số sau nó không đủ 3 chữ số — khi đó `,` là dấu thập phân
    (`"1,2"` → phần nguyên 1, phần thập phân "2"), không phải dấu ngăn hàng
    nghìn, và `frac` giữ `(phần_nguyên, chuỗi_phần_thập_phân)`.
    """
    out: List[Tuple[str, object, object]] = []
    s = text or ""
    n = len(s)
    i = 0
    while i < n:
        ch = s[i]
        if ch.isdigit():
            groups: List[str] = []
            seps: List[str] = []
            cur: List[str] = []
            j = i
            while j < n:
                c = s[j]
                if c.isdigit():
                    cur.append(c)
                    j += 1
                elif c in ".," and j + 1 < n and s[j + 1].isdigit():
                    groups.append("".join(cur))
                    seps.append(c)
                    cur = []
                    j += 1
                else:
                    break
            groups.append("".join(cur))
            value = int("".join(groups))
            frac = None
            if seps and seps[-1] == "," and len(groups[-1]) != 3:
                whole = int("".join(groups[:-1])) if len(groups) > 1 else 0
                frac = (whole, groups[-1])
            out.append(("num", value, frac))
            i = j
            continue
        if ch.isalpha():
            j = i
            while j < n and s[j].isalpha():
                j += 1
            out.append(("word", s[i:j].lower(), None))
            i = j
            continue
        if ch == "-":
            out.append(("dash", None, None))
            i += 1
            continue
        i += 1
    return out


def _decimal_value(token: Tuple[str, object, object]) -> float:
    """Giá trị số thực của một token `num`: dùng `frac` nếu `,` là dấu thập phân."""
    _, value, frac = token
    if frac is None:
        return float(value)
    whole, frac_digits = frac
    return whole + int(frac_digits) / (10 ** len(frac_digits))


def scan_money(text: str) -> List[int]:
    """Số tiền VND theo thứ tự xuất hiện trong `text`, không dùng regex.

    Một số tiền = một số đứng ngay trước đơn vị trong
    `("k", "nghìn", "ngàn", "đ", "đồng", "triệu", "tr")` (dính liền hay cách
    đều được). `"1 triệu 2"` / `"1tr2"` = 1 200 000. Trong khoảng
    `"X tới/đến/- Y đơn_vị"`, số X lấy đơn vị của Y. Số không có đơn vị →
    không phải tiền.
    """
    tokens = _tokenize_money(text)
    n = len(tokens)
    out: List[int] = []
    i = 0
    while i < n:
        kind = tokens[i][0]
        if kind != "num":
            i += 1
            continue
        j = i + 1

        # "X triệu/tr Y" — Y là phần sau: nếu có đơn vị nghìn/ngàn/k đứng
        # ngay sau Y thì Y là số đó theo đơn vị đó ("1 triệu 200 nghìn" =
        # 1 200 000); nếu không và Y chỉ một chữ số thì Y là chữ số hàng
        # trăm nghìn kiểu nói tắt ("1 triệu 2"/"1tr2" = 1 200 000).
        if (j < n and tokens[j][0] == "word" and tokens[j][1] in ("triệu", "tr")
                and j + 1 < n and tokens[j + 1][0] == "num"):
            second = tokens[j + 1][1]
            k = j + 2
            if k < n and tokens[k][0] == "word" and tokens[k][1] in ("nghìn", "ngàn", "k"):
                out.append(int(round(_decimal_value(tokens[i]) * 1_000_000 + second * 1_000)))
                i = k + 1
                continue
            if second < 10:
                out.append(int(round(_decimal_value(tokens[i]) * 1_000_000 + second * 100_000)))
                i = j + 2
                continue
            out.append(int(round(_decimal_value(tokens[i]) * 1_000_000)))
            i = j + 1
            continue

        # Khoảng "X tới/đến/- Y đơn_vị" → số X lấy đơn vị của Y
        is_range_connector = j < n and (
            tokens[j][0] == "dash" or (tokens[j][0] == "word" and tokens[j][1] in _RANGE_WORDS)
        )
        if is_range_connector and j + 1 < n and tokens[j + 1][0] == "num":
            k = j + 2
            if k < n and tokens[k][0] == "word" and tokens[k][1] in _MONEY_UNITS:
                mult = _MONEY_UNITS[tokens[k][1]]
                out.append(int(round(_decimal_value(tokens[i]) * mult)))
                out.append(int(round(_decimal_value(tokens[j + 1]) * mult)))
                i = k + 1
                continue
            i += 1
            continue

        # Số đứng ngay trước đơn vị tiền
        if j < n and tokens[j][0] == "word" and tokens[j][1] in _MONEY_UNITS:
            mult = _MONEY_UNITS[tokens[j][1]]
            out.append(int(round(_decimal_value(tokens[i]) * mult)))
            i = j + 1
            continue

        i += 1
    return out
