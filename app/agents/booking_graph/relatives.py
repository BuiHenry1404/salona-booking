"""Khách nhắc NGƯỜI NHÀ / người quen — lỗi live B3 #2.

"khoan da, de chu hoi lai no co ranh khong" từng được trả lời bằng
get_shop_status ("chủ tiệm đang rảnh"): BOOKING_PROMPT rule 1 bảo dùng tool tiệm
cho câu hỏi chủ tiệm bận hay rảnh, và model đọc "nó có rảnh không" thành câu đó.
"ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi nhan lai em sau
nha" thì ra câu từ chối quyền riêng tư (rule 4 — "hỏi về người khác").

Code nhận ra hai ca (không LLM, so cả có dấu lẫn không dấu):
- `asks_if_relative_free`: một người khách nhắc tới (nó, thằng/con, cháu, ông
  xã, bà nhà, vợ, chồng…) + "rảnh";
- `defers`: khách sẽ hỏi lại / tính lại rồi nhắn sau (dùng lại mẫu hoãn của
  `reply.py`).
Rồi: supervisor đưa vào booking không gọi model, và agent đọc MỘT dòng code
viết trong khối bối cảnh (`aside_line`, tag `[relative]`/`[deferral]` — tên
mà BOOKING_PROMPT rule 1/4 trỏ tới).
"""
import re

from app.agents.booking_graph.guard import repeats
from app.agents.booking_graph.reply import _DEFERRAL, _DEFERRAL_BARE, _has_time, _unaccented_only
from app.core.text import strip_diacritics

# "con" chỉ tính khi gõ CÓ dấu ở chỗ khác — bản không dấu "con ranh" là "còn
# rảnh" (hỏi giờ trống). "vo" không dấu cũng là "vô" — bỏ.
_PERSON = r"(?:nó|thằng|cháu|con|ông xã|bà xã|ông nhà|bà nhà|vợ|chồng|bố|mẹ)"
_PERSON_BARE = r"(?:no|thang|chau|ong xa|ba xa|ong nha|ba nha|chong)"
_GAP = r"(?:\s+\S+){0,3}?\s+"
_RELATIVE_FREE = re.compile(rf"\b{_PERSON}\b{_GAP}rảnh\b", re.IGNORECASE)
_RELATIVE_FREE_BARE = re.compile(rf"\b{_PERSON_BARE}\b{_GAP}ranh\b", re.IGNORECASE)
# Người của TIỆM đứng ngay trước: "chủ tiệm", "chị chủ" — không phải người nhà.
_SHOP_PERSON = re.compile(r"\b(?:chủ|chu|tiệm|tiem)\b", re.IGNORECASE)

_MESSAGE_LATER = re.compile(r"\bnhắn\s+(?:lại\s+)?(?:\w+\s+)?sau\b|\bnhan\s+(?:lai\s+)?(?:\w+\s+)?sau\b",
                            re.IGNORECASE)


def asks_if_relative_free(text: str) -> bool:
    """Khách hỏi một người KHÁCH nhắc tới có rảnh không (không phải chủ tiệm)."""
    lowered = (text or "").lower()
    bare = _unaccented_only(lowered)
    for pattern, source in ((_RELATIVE_FREE, lowered), (_RELATIVE_FREE_BARE, bare)):
        for m in pattern.finditer(source):
            if not _SHOP_PERSON.search(m.group(0)):
                return True
    return False


def defers(text: str) -> bool:
    """Khách sẽ hỏi ai đó / tính lại rồi nhắn sau."""
    lowered = (text or "").lower()
    bare = strip_diacritics(lowered)
    return bool(_DEFERRAL.search(lowered) or _DEFERRAL_BARE.search(bare)
                or _MESSAGE_LATER.search(lowered) or _MESSAGE_LATER.search(bare))


RELATIVE_LINE = ("Khách vừa hỏi NGƯỜI NHÀ/NGƯỜI QUEN của khách có rảnh không — không phải "
                 "hỏi chủ tiệm bận hay rảnh, không phải hỏi lịch của khách khác. [relative]")
DEFERRAL_LINE = ("Khách nói sẽ hỏi lại/tính lại rồi nhắn em sau — đây là chuyện lịch của "
                 "chính khách; chỉ đáp một câu ngắn, mời khách nhắn lại. [deferral]")


def aside_line(text: str) -> str:
    """Dòng code viết cho khối bối cảnh; rỗng khi câu khách không thuộc hai ca."""
    lines = [line for hit, line in ((asks_if_relative_free(text), RELATIVE_LINE),
                                    (defers(text), DEFERRAL_LINE)) if hit]
    return "\n".join(lines)


# --- câu đáp CODE viết cho câu hoãn (lỗi live B4 #2) ---------------------------
# Live B4 lượt 5 ("ko, chu hoi thang chau co ranh khong a. thoi de chu hoi no roi
# nhan lai em sau nha"): supervisor route bằng code, khối bối cảnh có đủ dòng
# [relative]/[deferral], vậy mà model vẫn đáp câu từ chối của rule 4 ("em chỉ
# xem và đặt lịch của anh Hùng thôi"). Rule 4 viết HOA, đứng trước vế loại
# trừ — prompt thua. Câu hoãn KHÔNG kèm yêu cầu nào thì không cần model: code
# đáp một câu ngắn mời khách nhắn lại. Kèm yêu cầu (có giờ, đặt/giữ/dời/hủy,
# hỏi tiệm) → vẫn để model lo.
_REQUEST = re.compile(
    r"\b(?:đặt|giữ|dời|đổi|hủy|huỷ|book|mấy giờ|giờ nào|đóng cửa|mở cửa|giá|bao nhiêu"
    r"|bao lâu|còn chỗ|còn trống)\b", re.IGNORECASE)
# Không dấu: động từ phải có bổ ngữ quen ("huy lich", "dat luon") — "huy" trần
# là tên (thằng Huy), "doi" trần còn là "đợi".
_REQUEST_BARE = re.compile(
    r"\b(?:(?:dat|giu|doi|huy)\s+(?:lich|cho|hen|luon|giup|gium|dum|lai|qua|sang|truoc|di|cai)"
    r"|book|may gio|gio nao|dong cua|mo cua|bao nhieu|bao lau|con cho|con trong)\b",
    re.IGNORECASE)

_DEFERRAL_REPLIES = (
    "Dạ vâng, {address} cứ thong thả, xong thì nhắn em nhé.",
    "Dạ, khi nào {address} sẵn sàng thì nhắn lại em là được ạ.",
    "Dạ không sao ạ, có gì {address} cứ nhắn em nhé.",
)


def has_request(text: str) -> bool:
    """Câu khách có kèm một yêu cầu cần model (giờ, đặt/giữ/dời/hủy, hỏi tiệm)."""
    lowered = (text or "").lower()
    return bool(_REQUEST.search(lowered) or _REQUEST_BARE.search(_unaccented_only(lowered))
                or _has_time(text or ""))


def deferral_reply(text: str, address: str, previous_replies) -> "str | None":
    """Câu đáp code viết khi khách chỉ hoãn ("để chú hỏi nó rồi nhắn lại em sau");
    None khi không phải câu hoãn hoặc câu có kèm yêu cầu. Không lặp câu vừa nói."""
    if not defers(text) or has_request(text):
        return None
    options = [r.format(address=address) for r in _DEFERRAL_REPLIES]
    previous = [p for p in previous_replies or [] if p]
    return next((o for o in options if o not in previous and not repeats(o, previous)), options[-1])
