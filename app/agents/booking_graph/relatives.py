"""Khách nhắc NGƯỜI NHÀ / người quen — lỗi live B3 #2, B4 #2 (0 regex).

Nhận diện qua `TurnAnalysis.aside` (nếu đã có bản phân tích đầu lượt) kết hợp
kiểm tra từ/cụm từ thuần Python qua `tokens.py` cho lối tắt trước LLM ở
`supervisor.py` và `make_agent_node`.
"""
from typing import Any, Mapping, Optional, Sequence

from app.agents.booking_graph import guard, tokens

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
    return next((o for o in options if o not in previous and not guard.repeats(o, previous)), options[-1])
