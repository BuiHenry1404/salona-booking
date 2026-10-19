"""Tầng gác: kiểm câu trả lời bằng CODE trước khi phát cho khách.

Ba phép kiểm: repeat, pronoun, clock (+ content sau rewrite). Đều không sửa
được bằng prompt — kể cả viết hoa, temperature 0.6, hay trích câu cũ vào khối
bối cảnh. Ở đây phát hiện tất định; LLM chỉ dùng để viết lại (rewrite.py), tối
đa một lần. Spec: 2026-09-14-natural-voice-guard-design.md.

`language` bỏ 2026-09-14 — chưa quan sát thấy với model này, prompt đã ghim
sẵn ngôn ngữ đầu ra.
"""
import re
from difflib import SequenceMatcher
from typing import Awaitable, Callable, List, Optional, Sequence

from app.agents.booking_graph.context import address_phrase, derive_address
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

REPEAT_RATIO = 0.85          # điểm khởi đầu — khoá sau khi duyệt scripts/probe_repeats.py
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 8         # đo trên transcript chạy thật 2026-09-19: 6 đã bắt hết,
                            # 8 lấy biên. Mốc cũ 3 mù với câu từ chối tái xuất
                            # sau vài lượt — LLM-32 lặp ở lượt 1, 2, 6, 14, 16.
REWRITE_TIMEOUT_SECONDS = 8
PHRASE_TIMEOUT_SECONDS = 8

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# Khách xin nhắc lại thì lặp là đúng — cùng vế miễn trừ của _NO_REPEAT.
_REPEAT_REQUESTS = ("nhắc lại", "nói lại", "lặp lại", "quên rồi", "quên mất")

# Đại từ trái giọng "em — anh/chị". "con" chỉ bắt khi làm chủ ngữ của một
# động từ lễ tân, để "con gái", "con nít" không bị oan.
#
# Sentence-start register (cô/chú/bác làm CHỦ NGỮ, VD "Cô muốn...") được kiểm
# bằng token đầu câu trong check_pronoun, không phải regex riêng — cùng một
# vòng lặp bỏ tiểu từ mở đầu ("Dạ") với phần đại từ sai giới. Chỉ tính honorific
# VIẾT HOA đúng kiểu câu Việt (đầu câu viết hoa): "cô gái đó" chữ thường không
# tính, "Cô muốn đặt giờ nào ạ?" thì tính.
_FORBIDDEN_REGISTER = {"Cô", "Chú", "Bác"}
# Tiểu từ mở đầu câu theo văn phong lễ tân — đứng trước đại từ/register thật sự
# nên phải bỏ qua trước khi soi token đầu câu, không thì "Dạ chị ..." lọt lưới.
_LEADING_PARTICLES = {"dạ", "vâng", "ừ", "à", "ờ"}
# `re.I` trên CẢ pattern làm [A-ZĐ] khớp cả chữ thường — "bác sĩ", "cô gái",
# "chú chó" bị coi là gọi khách sai giọng dù chỉ là danh từ thường. Chỉ có
# honorific mới không phân biệt hoa thường; tên đi sau PHẢI viết hoa mới tính.
_REGISTER_BEFORE_NAME = re.compile(r"\b(?i:cô|chú|bác)\s+[A-ZĐ][a-zà-ỹ]+")
# "cho con chị" (đặt hộ con của khách) không phải xưng hô sai — chỉ bắt "con"
# khi nó là CHỦ NGỮ của một động từ lễ tân ("con xem/đặt/...", "để/giúp con xem/...").
_CON_AS_SUBJECT = re.compile(r"\bcon\s+(xem|giúp|đặt|hỏi|kiểm|giữ)\b|\b(để|giúp)\s+con\s+(xem|đặt|kiểm|giữ|hỏi)\b", re.I)

_NUMBER_WORD = (r"(?:mười\s+(?:một|hai|ba|bốn|lăm|sáu|bảy|tám|chín)|"
                r"(?:hai|ba)\s+mươi(?:\s+(?:mốt|hai|ba|bốn|lăm|sáu|bảy|tám|chín))?|"
                r"mười|một|hai|ba|bốn|năm|sáu|bảy|tám|chín)")
_CLOCK_COLON = re.compile(r"\b\d{1,2}:\d{2}\b")
# KHÔNG `re.I`: chữ số viết bằng chữ trong câu đúng luật luôn là chữ THƯỜNG
# ("chín giờ sáng"); tên khách viết hoa ("Tám", "Ba", ...) không phải ca này.
_CLOCK_WORDS = re.compile(rf"\b{_NUMBER_WORD}\s+(?:giờ|tháng)\b|\bngày\s+{_NUMBER_WORD}\b")
# Alternation trong lookbehind của Python phải cùng độ dài — "anh", "em", "ông"
# khác số ký tự nhau, nên chặn tên khách sau xưng hô bằng cách soi phần văn
# bản đứng trước khớp, không dùng lookbehind biến thiên độ dài.
_HONORIFIC_BEFORE = re.compile(r"\b(?:anh|chị|em|cô|chú|bác|ông|bà)\s+$")

# Tên tool của agent booking/shop. Khách không bao giờ được thấy (UI "Không hiện
# tên tool"); A chưa lộ lần nào — luật này là PHÒNG NGỪA, xoá tất định bằng code,
# không gọi LLM thêm.
TOOL_NAMES = ("parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
              "cancel_appointment", "get_shop_hours", "get_shop_status")
_TOOL_ALT = "|".join(TOOL_NAMES)
_SNAKE_IDENT = r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"
# Nhóm ngoặc chứa tên tool hoặc định danh snake_case: "(parse_time, find_free_slots)".
_TOOL_GROUP = re.compile(
    rf"[ \t]*[(\[][^()\[\]]*?\b(?:{_TOOL_ALT}|{_SNAKE_IDENT})\b[^()\[\]]*[)\]]")
# Tên trần (có thể kèm backtick, "()" hoặc cả một danh sách nối bằng dấu phẩy/"và").
_BARE_NAME = rf"`?\b(?:{_TOOL_ALT})\b(?:\(\))?`?"
_TOOL_BARE = re.compile(rf"{_BARE_NAME}(?:[ \t]*(?:,|và|and)[ \t]*{_BARE_NAME})*")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([.,!?…;:])")

RewriteOnce = Callable[[str, List[str], Sequence[str], str], Awaitable[Optional[str]]]


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
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", _MULTI_SPACE.sub(" ", cleaned))
    # Dấu phẩy mồ côi ở đầu câu/sau dấu phẩy khác ("em dùng , để xem").
    cleaned = re.sub(r",(?:\s*,)+", ",", cleaned)
    return "\n".join(line.strip() for line in cleaned.split("\n")).strip()


_DATETIME = re.compile(r"Thứ\s+\w+\s+\d{1,2}/\d{1,2}|Chủ\s+Nhật\s+\d{1,2}/\d{1,2}|\d{1,2}\s+giờ(?:\s+rưỡi|\s+\d{2})?(?:\s+(?:sáng|chiều|tối))?")
_NUMBER = re.compile(r"\d+")


def normalize(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def sentences(text: str) -> List[str]:
    parts = [p.strip().rstrip(".!?…").strip() for p in _SENTENCE_END.split((text or "").strip())]
    return [p for p in parts if p]


def _digits(text: str) -> set:
    return set(re.findall(r"\d+", text or ""))


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
    """Token của câu, đã bỏ dấu câu dính từng từ (",", "?", ...) và bỏ các
    tiểu từ mở đầu ("Dạ", "Vâng", ...) — cả hai đều từng che mất token thật sự
    cần soi ở đầu câu."""
    tokens = [t for t in (_PUNCT.sub("", tok) for tok in sentence.split()) if t]
    i = 0
    while i < len(tokens) and tokens[i].lower() in _LEADING_PARTICLES:
        i += 1
    return tokens[i:]


def check_pronoun(draft: str, address: str, name: str) -> bool:
    """Gộp hai lỗi đại từ vào một bảng: giọng cô/chú/bác (tuyệt đối cấm) và
    đại từ sai giới so với `address` (VD "anh Tám" mà gọi "chị")."""
    if _REGISTER_BEFORE_NAME.search(draft) or _CON_AS_SUBJECT.search(draft):
        return True
    _, wrong = _pronouns(address)
    # Đầu câu (sau khi bỏ tiểu từ mở đầu), trừ khi nói về chủ tiệm ("Chị chủ ...").
    for sent in sentences(draft):
        tokens = _leading_content_tokens(sent)
        if not tokens:
            continue
        if tokens[0] in _FORBIDDEN_REGISTER:
            return True
        first = tokens[0].lower()
        nxt = tokens[1].lower() if len(tokens) > 1 else ""
        if first in wrong and nxt != "chủ":
            # "anh chị" nguyên cụm CHÍNH LÀ cách gọi đã chốt, không phải đoán giới.
            if first == "anh" and nxt == "chị":
                continue
            return True
    if not wrong:
        return False
    # Ngay trước tên khách.
    if name and any(re.search(rf"\b{w}\s+{re.escape(name)}\b", draft, re.I) for w in wrong):
        return True
    return False


def check_clock(draft: str) -> bool:
    if _CLOCK_COLON.search(draft):
        return True
    for m in _CLOCK_WORDS.finditer(draft):
        if _HONORIFIC_BEFORE.search(draft, 0, m.start()):
            continue
        return True
    return False


def content_kept(original: str, rewritten: str) -> bool:
    """Bản viết lại phải còn mọi mốc ngày-giờ và mọi con số của bản gốc."""
    for m in _DATETIME.findall(original):
        if m not in rewritten:
            return False
    return all(n in _NUMBER.findall(rewritten) for n in _NUMBER.findall(original))


def find_violations(draft: str, *, previous_replies: Sequence[str], address: str, name: str,
                    customer_text: str) -> List[str]:
    codes: List[str] = []
    if not customer_asked_to_repeat(customer_text) and repeats(draft, previous_replies):
        codes.append("repeat")
    if check_pronoun(draft, address, name):
        codes.append("pronoun")
    if check_clock(draft):
        codes.append("clock")
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
    address = address_phrase(user.full_name)
    _, name = derive_address(user.full_name)
    raw = state.get("draft") or ""
    draft = strip_tool_names(raw)
    if draft != raw:
        logger.warning("guard_tool_name_stripped")

    fact = state.get("phrase_fact") or state.get("confirm_fact")
    if fact:
        out = _guard_phrase(state, draft, fact, address)
        return {**out, "answer": strip_tool_names(out["answer"])}

    previous = state.get("previous_replies") or []
    customer_text = state.get("customer_text") or ""
    codes = find_violations(draft, previous_replies=previous, address=address, name=name,
                            customer_text=customer_text)
    if not codes:
        return {"answer": draft, "answer_source": "llm", "violations": []}

    logger.info("guard_violation", extra={"codes": codes})
    new = await rewrite_once(draft, codes, previous, state.get("context_block") or "")
    rewritten = strip_tool_names(new) if new else draft
    second = find_violations(rewritten, previous_replies=previous, address=address, name=name,
                             customer_text=customer_text)
    if not content_kept(draft, rewritten):
        second.append("content")
    return _after_rewrite(draft, rewritten, codes=second, rewrite_ran=rewritten != draft)


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
                           customer_text=state.get("customer_text") or "")
    if bad:
        logger.info("phrase_fallback", extra={"codes": bad})
        return {"answer": fallback, "answer_source": "code", "violations": bad}
    return {"answer": draft, "answer_source": "code" if draft == fallback else "llm", "violations": []}
