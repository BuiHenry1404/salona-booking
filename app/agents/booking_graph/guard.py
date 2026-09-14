"""Tầng gác: kiểm câu trả lời bằng CODE trước khi phát cho khách.

Bốn lỗi đo được ngày 2026-09-14 (lặp, xưng hô trượt, giọng cô/chú, giờ viết
sai) đều không sửa được bằng prompt — kể cả viết hoa, temperature 0.6, hay trích
câu cũ vào khối bối cảnh. Ở đây phát hiện tất định; LLM chỉ dùng để viết lại
(rewrite.py), tối đa một lần. Spec: 2026-09-14-natural-voice-guard-design.md.
"""
import re
from difflib import SequenceMatcher
from typing import List, Sequence

from app.agents.booking_graph.context import address_phrase, derive_address
from app.agents.booking_graph.state import GraphState
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

REPEAT_RATIO = 0.85          # điểm khởi đầu — khoá sau khi duyệt scripts/probe_repeats.py
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 3
ENGLISH_RATIO = 0.30
REWRITE_TIMEOUT_SECONDS = 8
PHRASE_TIMEOUT_SECONDS = 8

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_VI_DIACRITIC = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.I)

# Khách xin nhắc lại thì lặp là đúng — cùng vế miễn trừ của _NO_REPEAT.
_REPEAT_REQUESTS = ("nhắc lại", "nói lại", "lặp lại", "quên rồi", "quên mất")

# Đại từ trái giọng "em — anh/chị". "con" chỉ bắt khi làm chủ ngữ của một
# động từ lễ tân, để "con gái", "con nít" không bị oan.
_REGISTER_START = re.compile(r"(?:^|[.!?…]\s+)(cô|chú|bác)\b", re.I)
_REGISTER_BEFORE_NAME = re.compile(r"\b(cô|chú|bác)\s+[A-ZĐ][a-zà-ỹ]+", re.I)
# "cho con chị" (đặt hộ con của khách) không phải xưng hô sai — chỉ bắt "con"
# khi nó là CHỦ NGỮ của một động từ lễ tân ("con xem/đặt/...", "để/giúp con xem/...").
_CON_AS_SUBJECT = re.compile(r"\bcon\s+(xem|giúp|đặt|hỏi|kiểm|giữ)\b|\b(để|giúp)\s+con\s+(xem|đặt|kiểm|giữ|hỏi)\b", re.I)

_NUMBER_WORD = (r"(?:mười\s+(?:một|hai|ba|bốn|lăm|sáu|bảy|tám|chín)|"
                r"(?:hai|ba)\s+mươi(?:\s+(?:mốt|hai|ba|bốn|lăm|sáu|bảy|tám|chín))?|"
                r"mười|một|hai|ba|bốn|năm|sáu|bảy|tám|chín)")
_CLOCK_COLON = re.compile(r"\b\d{1,2}:\d{2}\b")
_CLOCK_WORDS = re.compile(rf"\b{_NUMBER_WORD}\s+(?:giờ|tháng)\b|\bngày\s+{_NUMBER_WORD}\b", re.I)

_ENGLISH = {"the", "you", "your", "please", "appointment", "booking", "book", "hello", "thanks",
            "thank", "time", "today", "tomorrow", "we", "is", "are", "can", "hair", "nail",
            "salon", "open", "closed", "at", "for", "and", "sure", "i", "that", "would", "like"}

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
    """"anh Tám" → (đúng "anh", sai "chị"); "chị Lan" → ngược lại; "anh chị" → không kiểm."""
    head = (address or "").split()[0].lower() if address else ""
    if head == "anh" and address.strip().lower() != "anh chị":
        return "anh", "chị"
    if head == "chị":
        return "chị", "anh"
    return None, None


def check_address(draft: str, address: str, name: str) -> bool:
    _, wrong = _pronouns(address)
    if not wrong:
        return False
    # Đầu câu, trừ khi nói về chủ tiệm ("Chị chủ ...").
    for sent in sentences(draft):
        first = sent.split()[:2]
        if first and first[0].lower() == wrong and not (len(first) > 1 and first[1].lower() == "chủ"):
            return True
    # Ngay trước tên khách.
    if name and re.search(rf"\b{wrong}\s+{re.escape(name)}\b", draft, re.I):
        return True
    return False


def check_register(draft: str) -> bool:
    return bool(_REGISTER_START.search(draft) or _REGISTER_BEFORE_NAME.search(draft)
                or _CON_AS_SUBJECT.search(draft))


def check_clock(draft: str) -> bool:
    return bool(_CLOCK_COLON.search(draft) or _CLOCK_WORDS.search(draft))


def check_language(draft: str) -> bool:
    words = normalize(draft).split()
    if len(words) <= 6:
        return False
    if not _VI_DIACRITIC.search(draft):
        return True
    english = sum(1 for w in words if w in _ENGLISH)
    return english / len(words) > ENGLISH_RATIO


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
    if check_address(draft, address, name):
        codes.append("address")
    if check_register(draft):
        codes.append("register")
    if check_clock(draft):
        codes.append("clock")
    if check_language(draft):
        codes.append("language")
    return codes


def make_guard_node(user: User):
    """Nơi DUY NHẤT set `answer`. Lần 1: vi phạm → xin rewrite. Lần 2: vi phạm
    (kể cả mất số liệu) → trả draft GỐC, không phải bản rewrite đã sai."""
    address = address_phrase(user.full_name)
    _, name = derive_address(user.full_name)

    async def node(state: GraphState) -> dict:
        draft = state.get("draft") or ""
        fact = state.get("phrase_fact")
        if fact:
            return _guard_phrase(state, draft, fact, address)          # Task 4

        codes = find_violations(
            draft, previous_replies=state.get("previous_replies") or [],
            address=address, name=name, customer_text=state.get("customer_text") or "",
        )
        if state.get("rewritten"):
            original = state.get("original_draft") or draft
            if not content_kept(original, draft):
                codes.append("content")
            if codes:
                logger.warning("guard_gave_up", extra={"codes": codes})
                return {"answer": original, "answer_source": "llm", "violations": codes}
            logger.info("guard_rewritten")
            return {"answer": draft, "answer_source": "llm", "violations": []}

        if codes:
            logger.info("guard_violation", extra={"codes": codes})
            return {"violations": codes}
        return {"answer": draft, "answer_source": "llm", "violations": []}

    return node


def _guard_phrase(state, draft, fact, address):
    """Thay ở Task 4. Task 2: chưa có phrase — không bao giờ tới đây."""
    return {"answer": draft, "answer_source": "llm", "violations": []}


def route_after_guard(state: GraphState) -> str:
    return "end" if "answer" in state and state.get("answer") else "rewrite"
