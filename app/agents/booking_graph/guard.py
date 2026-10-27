"""Tầng gác: kiểm câu trả lời bằng CODE trước khi phát cho khách.

Ba phép kiểm: repeat, pronoun, clock (+ content sau rewrite). Đều không sửa
được bằng prompt — kể cả viết hoa, temperature 0.6, hay trích câu cũ vào khối
bối cảnh. Ở đây phát hiện tất định; LLM chỉ dùng để viết lại (rewrite.py), tối
đa một lần. Spec: 2026-09-14-natural-voice-guard-design.md.

`language` bỏ 2026-09-14 — chưa quan sát thấy với model này, prompt đã ghim
sẵn ngôn ngữ đầu ra.
"""
from datetime import datetime
from difflib import SequenceMatcher
import json
import re
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from app.agents.booking_graph import tokens
from app.agents.booking_graph.context import (_clock_phrase, address_phrase, derive_address,
                                              format_vi_datetime, live_queue)
from app.core.clock import to_local
from app.core.config import settings
from app.core.logging import get_logger
from app.core.text import single_line
from app.models.user import User

logger = get_logger(__name__)

REPEAT_RATIO = 0.85          # điểm khởi đầu — khoá sau khi duyệt scripts/probe_repeats.py
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 8         # đo trên transcript chạy thật 2026-09-19: 6 đã bắt hết,
                            # 8 lấy biên. Mốc cũ 3 mù với câu từ chối tái xuất
                            # sau vài lượt — LLM-32 lặp ở lượt 1, 2, 6, 14, 16.
REWRITE_TIMEOUT_SECONDS = 8
PHRASE_TIMEOUT_SECONDS = 8

# Khách xin nhắc lại thì lặp là đúng — cùng vế miễn trừ của _NO_REPEAT.
_REPEAT_REQUESTS = ("nhắc lại", "nói lại", "lặp lại", "quên rồi", "quên mất")

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


def claim_facts(state) -> dict:
    """Những giờ được phép nói "đã giữ/đã đặt" ở lượt này — đọc từ chuỗi CODE
    viết (khối bối cảnh, kết quả tool, confirm_fact), không từ lời model."""
    spoken = [line for line in (state.get("context_block") or "").splitlines() if "[id:" in line]
    for message in state.get("messages") or []:
        if getattr(message, "type", "") != "tool" or getattr(message, "name", "") not in (
                "propose_appointment", "cancel_appointment"):
            continue
        try:
            data = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        for key in ("held", "will_cancel"):
            at = (data.get(key) or {}) if isinstance(data, dict) else {}
            at = at.get("at", at) if key == "will_cancel" else at
            if isinstance(at, dict) and at.get("spoken"):
                spoken.append(at["spoken"])
    fact = state.get("phrase_fact") or state.get("confirm_fact") or {}
    if fact.get("when"):
        spoken.append(fact["when"])
    held_unknown = bool(state.get("proposal_id")) and not any(
        getattr(m, "type", "") == "tool" for m in state.get("messages") or [])
    return {"clocks": [k for s in spoken for k in clock_keys(s)], "any": bool(spoken),
            "unknown": held_unknown}


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


def _held_iso(state) -> Optional[str]:
    for message in reversed(state.get("messages") or []):
        if getattr(message, "type", "") == "tool" and getattr(message, "name", "") == "propose_appointment":
            try:
                return json.loads(message.content)["held"]["iso"]
            except (TypeError, ValueError, KeyError):
                return None
    return None


def _mentions(text: str, keys: Sequence[tuple]) -> bool:
    return any(_same_clock(s, k) for s in clock_keys(text) for k in keys)


def announce_queue(answer: str, state, address: str = "anh chị") -> str:
    queue = live_queue(state.get("queued"))
    held = _held_iso(state)
    if not state.get("proposal_id") or not queue or not held:
        return answer
    held_at = to_local(datetime.fromisoformat(held))
    held_clock = _clock_phrase(held_at.hour, held_at.minute)
    held_keys = clock_keys(held_clock)
    items, queued_keys = [], []
    for item in queue:
        at = to_local(datetime.fromisoformat(item["start_at"]))
        clock = _clock_phrase(at.hour, at.minute)
        # Cùng giờ với lịch đang giữ (khác ngày) thì không phân biệt được bằng
        # giờ — không lấy làm dấu để bỏ câu.
        queued_keys += [k for k in clock_keys(clock) if not _mentions(clock, held_keys)]
        when = clock if at.date() == held_at.date() else format_vi_datetime(at)
        note = single_line(item.get("note"), 80) if item.get("note") else ""
        items.append(f"lịch {when} {note}".strip())

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
    body = [s.strip() for s in body if not _mentions(s, queued_keys)]
    if question is None or _mentions(question, queued_keys) or not _mentions(question, held_keys):
        question = f"{address[:1].upper()}{address[1:]} xác nhận lịch {held_clock} giúp em nhé?"
    if not body:
        body = [f"Em đang giữ lịch {format_vi_datetime(held_at)}."]
    line = f"Em giữ lịch này trước, xong em giữ tiếp {', '.join(items)} nhé."
    return " ".join([*body, line, question])


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


def customer_asked_to_repeat(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in _REPEAT_REQUESTS)


def _pronouns(address: str) -> tuple:
    """"anh Tám" → (đúng "anh", sai {"chị"}); "chị Lan" → ngược lại.

    "anh chị" → (None, {"anh", "chị"}): KHÔNG phải "không kiểm gì". Cách gọi đó
    nghĩa là tên khách không cho biết giới tính, nên gọi riêng "anh" hay "chị"
    là AI TỰ ĐOÁN giới tính khách — đúng thứ `derive_address` cố tình không làm.
    Đổi 2026-09-19 sau khi chạy thật: cùng một khách bị gọi "Chị ơi" hai lượt
    đầu rồi "Anh chị ơi" từ lượt 3.
    """
    head = (address or "").split()[0].lower() if address else ""
    if (address or "").strip().lower() == "anh chị":
        return None, frozenset({"anh", "chị"})
    if head == "anh":
        return "anh", frozenset({"chị"})
    if head == "chị":
        return "chị", frozenset({"anh"})
    return None, frozenset()


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


def find_violations(draft: str, *, previous_replies: Sequence[str], address: str, name: str,
                    customer_text: str, claims: Optional[dict] = None,
                    has_proposal: bool = True, voice_rules: bool = True) -> List[str]:
    """`voice_rules=False` (cờ `GUARD_VOICE_RULES`, Task 15): bỏ ba phép kiểm
    GIỌNG (`repeat`/`pronoun`/`clock`) để đo ảnh hưởng tới điểm "tự nhiên" của
    judge — benchmark trước khi đổi mặc định. `claim` (nói sai sự thật) và
    `ask_without_proposal` (Task 9, lỗi LLM-02) không phải chuyện giọng, luôn
    chạy; tên tool cũng luôn bị xoá, ở `apply_guard`, không ở đây."""
    codes: List[str] = []
    if has_false_claim(draft, claims):
        codes.append("claim")
    if voice_rules and not customer_asked_to_repeat(customer_text) and repeats(draft, previous_replies):
        codes.append("repeat")
    if voice_rules and check_pronoun(draft, address, name):
        codes.append("pronoun")
    if voice_rules and check_clock(draft):
        codes.append("clock")
    if not has_proposal and asks_yes_no_about_a_clock(draft):
        codes.append("ask_without_proposal")
    return codes


def _after_rewrite(original: str, rewritten: str, *, codes: List[str],
                   rewrite_ran: bool) -> dict:
    """Lần kiểm thứ hai (sau đúng MỘT lần rewrite): `codes` là vi phạm của bản
    viết lại, đã cộng "content" nếu mất số liệu. Không viết lại lần hai."""
    if codes:
        extra = {"codes": codes, "rewritten": True}
        if not rewrite_ran:
            # `rewrite` chạy nhưng không đổi được gì — draft vẫn là bản gốc.
            # Phân biệt với ca rewrite ĐỔI xong rồi vẫn sai, vì hai nguyên
            # nhân khác nhau: model không sửa được, hay sửa sai.
            extra["rewrite_ran"] = False
        # Bản GỐC an toàn hơn cho mọi lỗi — trừ đúng một ca: lỗi LẶP. Ở đó bản
        # gốc CHÍNH LÀ câu lặp, trả nó về là phát đúng thứ vừa bị bắt. Bản viết
        # lại dù chưa đạt thì ít nhất cũng khác chữ. Chỉ áp dụng khi lặp là lỗi
        # DUY NHẤT: còn "content" (mất số liệu), "pronoun" hay "clock" thì bản
        # gốc vẫn thắng.
        only_repeat = set(codes) == {"repeat"} and rewritten != original
        shipped = rewritten if only_repeat else original
        extra["shipped"] = "rewrite" if only_repeat else "original"
        logger.warning("guard_gave_up", extra=extra)
        return {"answer": shipped, "answer_source": "llm", "violations": codes}
    logger.info("guard_rewritten")
    return {"answer": rewritten, "answer_source": "llm", "violations": []}


async def apply_guard(state, user: User, rewrite_once: RewriteOnce) -> dict:
    """Nơi DUY NHẤT quyết câu trả lời (`answer`) — GuardMiddleware gọi hàm này.

    - Câu chốt lịch (`phrase_fact`/`confirm_fact`): sai số liệu → câu cứng
      `fallback` ngay, không rewrite.
    - Câu thường: vi phạm → `rewrite_once` ĐÚNG MỘT lần; bản viết lại vẫn vi
      phạm (kể cả mất số liệu) → trả draft GỐC, trừ ca chỉ lỗi `repeat`.

    Tên tool bị xoá (tất định, không gọi LLM) khỏi draft TRƯỚC mọi phép kiểm và
    khỏi mọi câu đi ra — kể cả bản viết lại và câu cứng.
    """
    address = address_phrase(user)
    _, name = derive_address(user.full_name)
    raw = state.get("draft") or ""
    draft = strip_tool_names(raw)
    if draft != raw:
        logger.warning("guard_tool_name_stripped")

    fact = state.get("phrase_fact") or state.get("confirm_fact")
    if fact:
        out = _guard_phrase(state, draft, fact, address)
        answer = strip_tool_names(out["answer"])
        if fact.get("then"):
            # Câu code viết (mời lịch kế trong hàng chờ) — nối SAU câu chốt đã kiểm.
            answer = f"{answer} {fact['then']}"
        return {**out, "answer": answer}

    if state.get("proposal_id"):
        fixed = unclaim(draft)
        if fixed != draft:
            logger.warning("guard_claim_rewritten")
            draft = fixed

    previous = state.get("previous_replies") or []
    customer_text = state.get("customer_text") or ""
    claims = claim_facts(state)
    has_proposal = bool(state.get("proposal_id"))
    voice_rules = settings.guard_voice_rules
    codes = find_violations(draft, previous_replies=previous, address=address, name=name,
                            customer_text=customer_text, claims=claims,
                            has_proposal=has_proposal, voice_rules=voice_rules)
    if not codes:
        return {"answer": announce_queue(draft, state, address), "answer_source": "llm", "violations": []}

    logger.info("guard_violation", extra={"codes": codes})
    new = await rewrite_once(draft, codes, previous, state.get("context_block") or "")
    rewritten = strip_tool_names(new) if new else draft
    second = find_violations(rewritten, previous_replies=previous, address=address, name=name,
                             customer_text=customer_text, claims=claims,
                             has_proposal=has_proposal, voice_rules=voice_rules)
    if not content_kept(draft, rewritten) and "claim" not in codes:
        second.append("content")
    out = _after_rewrite(draft, rewritten, codes=second, rewrite_ran=rewritten != draft)
    if has_false_claim(out["answer"], claims):
        # Rewrite không gỡ được lời nhận vơ: bỏ hẳn câu đó (tất định). Câu
        # sai sự thật tệ hơn mọi lỗi giọng.
        logger.warning("guard_claim_dropped")
        dropped = drop_false_claims(out["answer"], claims)
        out = {**out, "answer": dropped or f"Dạ, {address} cần em giữ chỗ giờ nào ạ?"}
    return {**out, "answer": announce_queue(out["answer"], state, address)}


def _guard_phrase(state, draft, fact, address):
    """Khoảnh khắc chốt lịch: sai số liệu là dùng câu cứng ngay, không rewrite."""
    fallback = state.get("fallback") or draft
    when, kind, error = fact.get("when"), fact.get("kind"), fact.get("error")
    bad = []
    if when and when not in draft:
        bad.append("when")
    if address != "anh chị" and address.lower() not in draft.lower():
        bad.append("address")
    if kind == "failed" and error and error.lower() not in draft.lower():
        bad.append("error")
    bad += find_violations(draft, previous_replies=[], address=address,
                           name=(address.split()[1] if address != "anh chị" and len(address.split()) > 1 else ""),
                           customer_text=state.get("customer_text") or "",
                           claims=claim_facts(state))
    if bad:
        logger.info("phrase_fallback", extra={"codes": bad})
        return {"answer": fallback, "answer_source": "code", "violations": bad}
    return {"answer": draft, "answer_source": "code" if draft == fallback else "llm", "violations": []}
