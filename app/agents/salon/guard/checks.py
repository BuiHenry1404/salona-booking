"""Phép kiểm câu trả lời cuối của Orchestrator (spec §7, docs/architech-agent.md
luật 4-5) — code thuần, không gọi model, không regex.

`check_structured` đọc câu trả lời JSON các đoạn trích dẫn (spec level C): parse →
ghép chữ → `strip_tool_text` (xoá tên tool / khối JSON lọt vào câu) → các luật;
trả mã vi phạm theo thứ tự cố định `CHECK_CODES`, mỗi mã tối đa một lần; rỗng =
câu được gửi khách. `check_text` là phần luật đọc trên chữ đã ghép.

Các tập từ ở đây là từ NGỮ PHÁP của câu bot (động từ "đặt / giữ / dời / huỷ",
"đã / rồi", phủ định "chưa / không", đại từ xưng hô) — guard đọc câu BOT nói về
việc của chính nó, không đoán ý định của khách (CONTEXT.md bẫy 21).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from app.agents.salon.guard.catalog import ANNOUNCE_ROLES
from app.agents.salon.guard.facts import (
    ClockKey,
    Reading,
    ReplyFacts,
    clocks_in_text,
    is_known,
)
from app.agents.salon.guard.segments import check_segments, join_segments, parse_reply, taken_not_told
from app.agents.salon.shared.text_scan import (
    digits_in,
    split_clauses,
    split_sentences,
    words,
)
from app.agents.salon.shared.tool_names import CONTROL_TOOLS, SUBAGENT_TOOLS
from app.core.config import settings
from app.core.text import normalize_for_match

# Mọi mã vi phạm có thể gặp — thứ tự cố định dùng để sắp `codes` của
# `check_structured` (spec level C §3: mã cấu trúc trước, rồi các luật đọc chữ).
# Giờ / giá không còn so trên chữ (`unknown_time` / `unknown_price` cũ): đoạn
# trích dẫn phải khớp dữ kiện (`ref_mismatch`), chữ thường không được nhắc
# (`unreferenced`).
CHECK_CODES = (
    "bad_structure", "unknown_ref", "wrong_role", "ref_mismatch", "unreferenced",
    "taken_not_told", "empty", "claim", "missing_fact", "missing_question",
    "missing_phone", "phone_not_allowed", "pending_undecided", "pronoun",
    "foreign_script", "english", "repeat",
)

REPEAT_RATIO = 0.85
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 2

# ---------------------------------------------------------------------------
# foreign_script / english — spec §6: câu trả lời phải là tiếng Việt, không
# lẫn chữ của chữ viết khác, và không phải một câu/đoạn tiếng Anh (vd lộ
# nguyên văn một dòng prompt).
# ---------------------------------------------------------------------------

ENGLISH_FUNCTION_WORDS = frozenset({"the", "is", "are", "was", "be", "of", "and", "or", "for", "with",
    "this", "that", "these", "you", "your", "we", "it", "in", "on", "at", "by", "from", "as", "if",
    "not", "does", "please", "will", "should", "must", "never", "only", "when", "what", "which",
    "there", "here"})
ENGLISH_RUN_MIN_WORDS = 4
ENGLISH_RUN_MIN_FUNCTION_WORDS = 2


def _has_foreign_script(text: str) -> bool:
    """Bất kỳ chữ cái nào KHÔNG thuộc chữ Latin (tên Unicode không bắt đầu
    bằng "LATIN") sau khi chuẩn hoá NFC — chữ Việt có dấu luôn là một chữ
    Latin duy nhất ở dạng NFC (vd "ầ" = LATIN SMALL LETTER A WITH CIRCUMFLEX
    AND GRAVE), nên câu NFD (chữ cái + dấu rời) phải chuẩn hoá trước khi so."""
    nfc = unicodedata.normalize("NFC", text)
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in nfc)


def _ascii_word_tokens(text: str) -> list[str]:
    """Tách `text` (đã NFC) thành token theo ký tự KHÔNG phải chữ-số (không
    regex); mỗi token giữ nguyên hoa/thường để kiểm `isascii()` / `isalpha()`."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isalnum():
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _has_english_run(text: str) -> bool:
    """Một dãy từ ASCII liên tiếp (≥ `ENGLISH_RUN_MIN_WORDS` từ, trong đó ≥
    `ENGLISH_RUN_MIN_FUNCTION_WORDS` từ là từ nối tiếng Anh) — câu tiếng Việt
    chen vài chữ tiếng Anh ngắn (tên dịch vụ, "ok") không đủ dài để bắt."""
    nfc = unicodedata.normalize("NFC", text)
    run_len = 0
    run_function_words = 0
    for token in _ascii_word_tokens(nfc):
        if token.isascii() and token.isalpha():
            run_len += 1
            if token.lower() in ENGLISH_FUNCTION_WORDS:
                run_function_words += 1
            if run_len >= ENGLISH_RUN_MIN_WORDS and run_function_words >= ENGLISH_RUN_MIN_FUNCTION_WORDS:
                return True
        else:
            run_len = 0
            run_function_words = 0
    return False


# ---------------------------------------------------------------------------
# claim — nhận vơ đã đặt / dời / huỷ, giữ chỗ không có thật
# ---------------------------------------------------------------------------

# Động từ cam kết (một hay hai từ) -> loại việc đã ghi cho phép nói nó.
# "đặt / ghi / chốt / xác nhận / lên lịch" cũng đúng sau một lần dời đã duyệt
# (lịch mới đã được ghi); "đổi" cũng đúng sau một lần đặt ("giờ đặt đã đổi
# thành …").
_CLAIMS_FOR_VERB: dict[tuple[str, ...], tuple[str, ...]] = {
    ("đặt",): ("booked", "moved"),
    ("ghi",): ("booked", "moved"),
    ("chốt",): ("booked", "moved"),
    ("xác", "nhận"): ("booked", "moved"),
    ("lên", "lịch"): ("booked", "moved"),
    ("dời",): ("moved",),
    ("đổi",): ("moved", "booked"),
    ("hủy",): ("cancelled",),
    ("huỷ",): ("cancelled",),
}
_HOLD = ("giữ",)
# Động từ ĐẶT: kể lại một lịch khách đang có (`existing_only_clocks`) là đúng
# (Ruling R8). Dời / đổi / huỷ thì không bao giờ.
_BOOKING_VERBS = frozenset({("đặt",), ("ghi",), ("chốt",), ("xác", "nhận"), ("lên", "lịch")})
_VERBS = sorted([*_CLAIMS_FOR_VERB, _HOLD], key=len, reverse=True)
_DONE_BEFORE = frozenset({"đã", "vừa", "đang"})
_DONE_AFTER = frozenset({"rồi", "xong"})
# Phủ định chỉ tính khi đứng NGAY TRƯỚC động từ ("chưa đặt", "không giữ") —
# "đã đặt xong rồi, chị không phải lo" vẫn là nhận đã đặt (review Task 12 #4).
_NEGATION = frozenset({"chưa", "không", "ko"})
# "có người đặt rồi": người khác làm, không phải em nhận.
_OTHER_SUBJECTS = frozenset({"người", "khách"})
# "sẽ giữ / sẽ huỷ": hứa làm, chưa phải nói đã làm.
_FUTURE = frozenset({"sẽ"})
# "ghi nhận / ghi lại" không phải ghi lịch.
_NOT_BOOKING_AFTER_GHI = frozenset({"nhận", "lại"})
# "đã đổi ý rồi", "đã xác nhận thông tin" không nói về lịch — các động từ này
# chỉ là nhận vơ khi mệnh đề nói tới lịch hay một giờ.
_NEEDS_APPOINTMENT = frozenset({("dời",), ("đổi",), ("hủy",), ("huỷ",), ("xác", "nhận")})
_APPOINTMENT_WORDS = frozenset({"lịch", "chỗ", "hẹn"})
# Cụm tách mệnh đề: "…, còn …" là hai việc khác nhau.
_CLAUSE_BREAKS = ("còn",)
# Mệnh đề cuối câu hỏi kết bằng "chưa (ạ)?" là hỏi ("chị đã đặt lịch chưa ạ?"),
# không phải nói đã làm.
_QUESTION_TAILS = (("chưa",), ("chưa", "ạ"))


def _verb_at(clause_words: list[str], i: int) -> Optional[tuple[str, ...]]:
    for verb in _VERBS:
        if tuple(clause_words[i:i + len(verb)]) == verb:
            return verb
    return None


def _claim_verbs(clause_words: list[str], has_clock: bool) -> set[tuple[str, ...]]:
    """Động từ cam kết mà mệnh đề nói là em ĐÃ / ĐANG làm: có "đã / vừa /
    đang" trong 2 từ trước, hoặc "rồi / xong" phía sau; riêng "giữ" còn tính ở
    dạng trần ("em giữ chỗ 4 giờ chiều") khi mệnh đề nói tới lịch hay giờ.
    Bỏ qua: phủ định trong 2 từ ngay trước động từ, chủ ngữ người khác,
    tương lai "sẽ"."""
    about_appointment = has_clock or any(word in _APPOINTMENT_WORDS for word in clause_words)
    verbs = set()
    for i in range(len(clause_words)):
        verb = _verb_at(clause_words, i)
        if verb is None:
            continue
        before = set(clause_words[max(0, i - 2):i])
        if before & (_OTHER_SUBJECTS | _FUTURE | _NEGATION):
            continue
        after = clause_words[i + len(verb):]
        if verb == ("ghi",) and after[:1] and after[0] in _NOT_BOOKING_AFTER_GHI:
            continue
        done = bool(before & _DONE_BEFORE) or any(w in _DONE_AFTER for w in after)
        if not (done or (verb == _HOLD and about_appointment)):
            continue
        if verb in _NEEDS_APPOINTMENT and not about_appointment:
            continue
        verbs.add(verb)
    return verbs


def _existing_vouches(clock: Reading, facts: ReplyFacts) -> bool:
    """Một giờ NÊU RA là một lịch khách đang có và không trùng giờ nào khác
    của lượt (`existing_only_clocks`, Ruling R9) — guard không so ngày: "3 giờ
    chiều thứ Tư" vừa duyệt hỏng không được ké lịch 3 giờ chiều thứ Ba."""
    return is_known(clock, facts.existing_only_clocks)


def _commit_ok(verb: tuple[str, ...], said: list[Reading], facts: ReplyFacts) -> bool:
    kinds = _CLAIMS_FOR_VERB[verb]
    done: set[ClockKey] = set().union(*(facts.done_claims.get(kind, set()) for kind in kinds))
    if not said:
        # Không nêu giờ: chỉ một việc ĐÃ ghi trong lượt / R0 mới bảo chứng —
        # lịch đang có thì không (mọi khách có lịch sẽ lọt).
        return bool(done)
    by_existing = verb in _BOOKING_VERBS
    if all(is_known(clock, done) or (by_existing and _existing_vouches(clock, facts))
           for clock in said):
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
    # Chỉ "giữ": hợp lệ khi giờ đó đang có đề xuất chờ thật, đã ghi, hoặc là
    # một lịch khách đang có ("em vẫn giữ lịch 3 giờ chiều mai cho chị"). Không
    # nêu giờ thì lịch đang có không bảo chứng.
    holdable = facts.held_clocks.union(*facts.done_claims.values())
    if said:
        return not all(is_known(clock, holdable) or _existing_vouches(clock, facts)
                       for clock in said)
    return not holdable


def _is_question_tail(clause: str) -> bool:
    clause_words = words(normalize_for_match(clause))
    return any(tuple(clause_words[-len(tail):]) == tail for tail in _QUESTION_TAILS)


def _has_false_claim(text: str, facts: ReplyFacts) -> bool:
    for sentence in split_sentences(text):
        clauses = split_clauses(sentence, break_words=_CLAUSE_BREAKS)
        if clauses and sentence.rstrip().endswith("?") and _is_question_tail(clauses[-1]):
            clauses = clauses[:-1]
        if any(_clause_claims_falsely(clause, facts) for clause in clauses):
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
# check_text
# ---------------------------------------------------------------------------


def _phone_digits() -> str:
    return "".join(digits_in(settings.shop_phone or ""))


def _mentions_phone(text: str) -> bool:
    phone = _phone_digits()
    return bool(phone) and phone in "".join(digits_in(text))


def check_text(text: str, facts: ReplyFacts) -> set[str]:
    """Mã vi phạm đọc trên CHỮ của câu trả lời (không cần dữ kiện trích dẫn
    cấu trúc) — `check_structured` chạy trên câu đã ráp (spec level C)."""
    text = text or ""
    found: set[str] = set()
    # Câu rỗng — vd model viết tool call thành chữ, `strip_tool_text` xoá sạch
    # (review Task 13): không bao giờ gửi khách một tin trống.
    if not text.strip():
        found.add("empty")
    if _has_false_claim(text, facts):
        found.add("claim")
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
    if _has_foreign_script(text):
        found.add("foreign_script")
    if _has_english_run(text):
        found.add("english")
    if _repeats(text, facts.previous_replies):
        found.add("repeat")
    return found


# ---------------------------------------------------------------------------
# check_structured — câu trả lời cấu trúc (level C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardResult:
    text: str                    # câu đã ráp + strip_tool_text; "" khi bad_structure
    codes: list[str]             # theo thứ tự CHECK_CODES
    problems: list[str]
    announce_turn_ids: list[str]


def check_structured(raw: str, facts: ReplyFacts) -> GuardResult:
    """Kiểm câu trả lời cấu trúc (JSON mảng đoạn, spec level C §2-§3): mỗi
    đoạn trích dẫn một dữ kiện của lượt (`facts.catalog`), và mọi dữ kiện
    BẮT BUỘC (`facts.required`) phải được trích dẫn đúng vai trò."""
    segments = parse_reply(raw)
    if segments is None:
        return GuardResult("", ["bad_structure"], ["the reply was not the JSON list of segments"], [])

    text = strip_tool_text(join_segments(segments))
    report = check_segments(segments, facts.catalog)

    missing_problems = [
        f"{item.ref}: {item.todo}"
        for item in facts.required
        if not any(ref == item.ref and role in item.roles for ref, role in report.cited)
    ]
    taken_problems = [
        f"{ref} is cited as taken but the sentence does not tell the customer it is already "
        "booked, or invites them to choose it"
        for ref in taken_not_told(segments, facts.catalog)
    ]

    found = set(report.codes) | check_text(text, facts)
    if missing_problems:
        found.add("missing_fact")
    if taken_problems:
        found.add("taken_not_told")
    codes = [code for code in CHECK_CODES if code in found]
    problems = [*report.problems, *taken_problems, *missing_problems]

    announce_turn_ids: list[str] = []
    for ref, role in report.cited:
        if role not in ANNOUNCE_ROLES:
            continue
        entry = facts.catalog.get(ref)
        if entry is None:
            continue
        for turn_id in entry.announce_turn_ids:
            if turn_id not in announce_turn_ids:
                announce_turn_ids.append(turn_id)

    return GuardResult(text=text, codes=codes, problems=problems, announce_turn_ids=announce_turn_ids)


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
    """Bỏ dấu in đậm Markdown `**` / `__` — khung chat hiện chữ thô (live run 2:
    8/10 câu có `**…**`). `*` lẻ, tiêu đề, gạch đầu dòng: chỉ luật prompt chặn."""
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
