"""Validator câu trả lời của Orchestrator (spec guard rebuild, mục "Validator
(validator.py)"): kiểm CHỮ của model (hoặc `templates.body`) CHỈ trên dữ kiện
trích được — giờ, giá, tên dịch vụ trong danh mục, chữ số điện thoại, dấu `?`,
loại ký tự, tool call. Không danh sách từ ngữ pháp: không bao giờ đoán nghĩa
từ hư từ tiếng Việt. Bộ từ con số (buổi, đơn vị tiền, "tới / đến") nằm trong
`text_scan.py` / `clocks.py`, không ở đây.

Câu báo kín do code ghép vào SAU khi kiểm, nên không bao giờ qua đây.

Chỉ import `clocks.py`, `results.py`, `planner.py` trong `guard/`. Không regex.
"""
from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Optional, Sequence

from pydantic import BaseModel

from app.agents.salon.guard.clocks import Reading, clocks_in_text, is_known
from app.agents.salon.guard.planner import PlanAct, ReplyPlan
from app.agents.salon.guard.results import TurnResults
from app.agents.salon.shared.text_scan import digits_in, scan_money, split_sentences, words
from app.agents.salon.shared.tool_names import CONTROL_TOOLS, SUBAGENT_TOOLS
from app.core.text import normalize_for_match

# Mọi mã vi phạm — thứ tự cố định dùng để sắp `Verdict.codes`.
CODES = ("bad_tool_call", "pending_undecided", "plan_missing", "unknown_time", "unknown_price",
         "wrong_service", "taken_mentioned", "missing_question", "missing_phone",
         "phone_not_allowed", "empty", "foreign_script", "repeat")

REPEAT_RATIO = 0.85
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 2


class Verdict(BaseModel):
    text: str                  # câu sau `strip_tool_text`
    codes: list[str]           # theo thứ tự `CODES`
    missing: list[dict]        # hành động còn thiếu (JSON như trong plan); `ask` -> {"ask": {...}}


# ---------------------------------------------------------------------------
# helper dữ kiện
# ---------------------------------------------------------------------------


def _match_words(text: str) -> list[str]:
    return words(normalize_for_match(text))


def _word_spans(haystack: Sequence[str], needle: Sequence[str]) -> list[tuple[int, int]]:
    """Mọi vị trí `[start, end)` mà `needle` nằm liên tiếp trong `haystack` —
    khớp tên dịch vụ theo dãy từ, không theo chuỗi con ("Uốn" không khớp bên
    trong "muốn")."""
    n = len(needle)
    if not n:
        return []
    return [(i, i + n) for i in range(len(haystack) - n + 1)
            if list(haystack[i:i + n]) == list(needle)]


def _names_word(text_words: list[str], name: str) -> bool:
    return bool(_word_spans(text_words, _match_words(name)))


def _says(said: list[Reading], clock: tuple[int, int]) -> bool:
    return any(clock in reading for reading in said)


def _act_json(act: PlanAct) -> dict:
    return {"act": act.act, **act.fields}


def _turn_names(results: TurnResults) -> list[str]:
    return [results.services[sid].name for sid in results.turn_service_ids
            if sid in results.services]


def _turn_prices(plan: ReplyPlan, results: TurnResults) -> list[tuple[int, int]]:
    ranges = [(results.services[sid].price_min, results.services[sid].price_max)
              for sid in results.turn_service_ids if sid in results.services]
    return ranges + [price for act in plan.acts for price in act.prices]


# ---------------------------------------------------------------------------
# các phép kiểm
# ---------------------------------------------------------------------------


def _missing_acts(said: list[Reading], amounts: list[int], text_words: list[str],
                  plan: ReplyPlan) -> list[dict]:
    """Hành động còn thiếu: một giờ của nó không có trong câu (giờ:phút có trong
    một cách đọc của giờ nào đó trong câu), một khoảng giá không có số tiền nào
    nằm trong, hay một tên dịch vụ (`proposal` / `done` / `price`) không có."""
    missing: list[dict] = []
    for act in plan.acts:
        lacks_clock = any(not _says(said, clock) for clock in act.clocks)
        lacks_price = any(not any(low <= amount <= high for amount in amounts)
                          for low, high in act.prices)
        lacks_name = any(not _names_word(text_words, name) for name in act.service_names)
        if lacks_clock or lacks_price or lacks_name:
            missing.append(_act_json(act))
    return missing


def _foreign_names(text_words: list[str], names: Sequence[str], allowed: Sequence[str]) -> list[str]:
    """Các tên trong `names` (không thuộc `allowed`) có mặt trong `text_words`
    ở một chỗ KHÔNG nằm trong một tên của `allowed` ("cắt tóc" bên trong
    "cắt tóc nữ" của lượt không phải dịch vụ khác)."""
    allowed_words = {tuple(_match_words(name)) for name in allowed}
    covered = [span for name in allowed_words for span in _word_spans(text_words, name)]
    found: list[str] = []
    for name in names:
        name_words = tuple(_match_words(name))
        if not name_words or name_words in allowed_words:
            continue
        if any(not any(start <= s and e <= end for start, end in covered)
               for s, e in _word_spans(text_words, name_words)):
            found.append(name)
    return list(dict.fromkeys(found))


def _phone_digits(phone: Optional[str]) -> str:
    return "".join(digits_in(phone or ""))


def _mentions(text: str, phone_digits: str) -> bool:
    return bool(phone_digits) and phone_digits in "".join(digits_in(text))


def _has_foreign_script(text: str) -> bool:
    """Bất kỳ chữ cái nào KHÔNG thuộc chữ Latin (tên Unicode không bắt đầu
    bằng "LATIN") sau khi chuẩn hoá NFC — chữ Việt có dấu luôn là một chữ
    Latin duy nhất ở dạng NFC, nên câu NFD phải chuẩn hoá trước khi so."""
    nfc = unicodedata.normalize("NFC", text)
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in nfc)


def _normalized_sentence(sentence: str) -> str:
    return " ".join(_match_words(sentence))


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
# validate
# ---------------------------------------------------------------------------


def validate(text: str, plan: ReplyPlan, results: TurnResults, *,
             invalid_tool_call: bool = False) -> Verdict:
    """Kiểm `text` (chữ model, hay `templates.body`) với kế hoạch và dữ kiện của
    lượt. `codes` rỗng = câu được gửi (sau câu báo kín của code)."""
    text = strip_tool_text(text or "")
    said = clocks_in_text(text)
    amounts = scan_money(text)
    text_words = _match_words(text)
    found: set[str] = set()

    if invalid_tool_call:
        found.add("bad_tool_call")
    if results.pending_undecided:
        found.add("pending_undecided")

    missing = _missing_acts(said, amounts, text_words, plan)
    if missing:
        found.add("plan_missing")
    if any(not is_known(reading, results.valid_clocks) for reading in said):
        found.add("unknown_time")
    ranges = _turn_prices(plan, results)
    if any(not any(low <= amount <= high for low, high in ranges) for amount in amounts):
        found.add("unknown_price")
    if _foreign_names(text_words, results.shop_service_names, _turn_names(results)):
        found.add("wrong_service")
    act_clocks = plan.act_clocks()
    if any(_says(said, clock) for clock in plan.taken_clocks if clock not in act_clocks):
        found.add("taken_mentioned")

    asks = plan.ask is not None or any(act.act in ("proposal", "offer") for act in plan.acts)
    if asks and "?" not in text:
        found.add("missing_question")
        # `ask` không có giờ / giá / tên bắt buộc: nó chỉ thiếu khi câu không hỏi.
        if plan.ask is not None:
            missing.append({"ask": plan.to_json()["ask"]})

    required_phone = _phone_digits(plan.phone)
    if required_phone and not _mentions(text, required_phone):
        found.add("missing_phone")
    if not required_phone and _mentions(text, _phone_digits(results.shop_phone)):
        found.add("phone_not_allowed")

    if not text.strip():
        found.add("empty")
    if _has_foreign_script(text):
        found.add("foreign_script")
    if _repeats(text, results.previous_replies):
        found.add("repeat")

    return Verdict(text=text, codes=[code for code in CODES if code in found], missing=missing)


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


def _drop_markdown(text: str) -> str:
    """Bỏ dấu in đậm Markdown `**` / `__` — khung chat hiện chữ thô."""
    return text.replace("**", "").replace("__", "")


def strip_tool_text(text: str) -> str:
    """Bỏ dấu in đậm Markdown, tên tool kỹ thuật (`CONTROL_TOOLS` + tool agent
    con) và khối JSON `{...}` khỏi câu sắp gửi khách, rồi gộp khoảng trắng. Câu
    sạch trả NGUYÊN VĂN (kể cả xuống dòng)."""
    if not text:
        return text
    cleaned = _drop_empty_brackets(_drop_tool_names(_drop_json(_drop_markdown(text))))
    if cleaned == text:
        return text
    return "\n".join(_tidy_line(line) for line in cleaned.split("\n")).strip()
