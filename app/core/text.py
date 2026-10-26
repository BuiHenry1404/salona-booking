import re
import unicodedata
from typing import Literal, Optional


def single_line(value: Optional[str], max_length: int) -> Optional[str]:
    """Ép một chuỗi của người dùng về ĐÚNG MỘT DÒNG rồi cắt theo độ dài.

    Mọi chữ tự do của khách đều chảy vào khối bối cảnh, mà khối đó mang vai
    HumanMessage và tự nói "hãy tin phần trên". Một chuỗi có xuống dòng vì thế
    là lối để khách tự ghi thêm một dòng luật vào prompt.

    `split()` không tham số gộp mọi loại khoảng trắng (space, \\n, \\r, \\t)
    thành một dấu cách — đúng thứ cần, và ngắn hơn một regex.

    Trả `None` thay vì chuỗi rỗng: khối bối cảnh in chuỗi rỗng ra thành một
    dấu gạch cụt lủn, còn `None` thì các hàm gọi đã có sẵn nhánh lùi.
    """
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:max_length] or None


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt. 'đúng rồi' và 'dung roi' phải cùng ra một chuỗi.

    NFD tách dấu thành ký tự riêng để lọc, nhưng đ/Đ không phải d kèm dấu — nó
    là một ký tự độc lập nên phải thay tay.
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


# --- khách tự xưng "chị"/"anh" (Task 13, dựng ở Task 12 vì sanitize_analysis
# cần) -------------------------------------------------------------------
# Khách lớn tuổi không nói "tôi", họ tự xưng bằng chính cách họ muốn được gọi:
# "chị đặt lịch" nghĩa là CHÍNH khách muốn đặt, không phải khách đang nói về
# một người phụ nữ khác. Hai mẫu: "chị/anh" + động từ ngôi thứ nhất, hoặc
# "cho/giúp/với" + "chị/anh" (nhờ vả cho chính mình). Chỉ nhận khi đúng MỘT
# trong hai đại từ khớp — "anh chị" đi liền nhau là lời chào chung, còn khớp
# cả "chị" lẫn "anh" trong cùng câu là mơ hồ, không đoán giới tính (bẫy "gọi
# anh chị là cố ý" — không suy luận bừa).
_SELF_VERBS = ("làm", "muốn", "đặt", "hủy", "dời", "đổi", "qua", "tới", "đến",
              "hỏi", "cần", "xem", "rảnh")
_SELF_VERBS_BARE = tuple(strip_diacritics(v) for v in _SELF_VERBS)

_SELF_PATTERN_A = re.compile(r"\b(chị|anh)\s+(" + "|".join(_SELF_VERBS) + r")\b",
                             re.IGNORECASE)
_SELF_PATTERN_B = re.compile(r"\b(cho|giúp|với)\s+(chị|anh)\b", re.IGNORECASE)
_SELF_PATTERN_A_BARE = re.compile(r"\b(chi|anh)\s+(" + "|".join(_SELF_VERBS_BARE) + r")\b",
                                  re.IGNORECASE)
_SELF_PATTERN_B_BARE = re.compile(r"\b(cho|giup|voi)\s+(chi|anh)\b", re.IGNORECASE)

_ANH_CHI_TOGETHER = re.compile(r"\b(anh\s+chị|chị\s+anh)\b", re.IGNORECASE)
_ANH_CHI_TOGETHER_BARE = re.compile(r"\b(anh\s+chi|chi\s+anh)\b", re.IGNORECASE)


def _bare_hit(token: str) -> str:
    return "chị" if token == "chi" else "anh"


def detect_self_address(text: Optional[str]) -> Optional[Literal["anh", "chị"]]:
    """Khách đang tự xưng "chị" hay "anh", suy từ chính câu khách gõ — không
    đoán giới tính từ tên (tên khách dạng "Nguyễn Thị Lan" không nói lên
    điều đó). Trả None khi không chắc: "em", "anh chị" đi liền nhau (lời
    chào chung), "chị/anh Tên Người" (ngôi thứ ba, chữ sau viết hoa), hoặc
    cả hai đại từ cùng khớp trong một câu.
    """
    if not text:
        return None
    lowered = text.lower()
    bare = strip_diacritics(lowered)
    typed_bare = bare == lowered  # khách gõ hoàn toàn không dấu

    if _ANH_CHI_TOGETHER.search(lowered):
        return None
    if typed_bare and _ANH_CHI_TOGETHER_BARE.search(bare):
        return None

    hits = set()
    for m in _SELF_PATTERN_A.finditer(lowered):
        hits.add(m.group(1))
    for m in _SELF_PATTERN_B.finditer(lowered):
        # Chữ ngay sau viết hoa trong câu GỐC (chưa lowercase) là tên riêng —
        # "giúp chị Hoa" là nhờ giúp một người thứ ba, không phải tự xưng.
        after = text[m.end():].lstrip()
        if after and after[0].isupper():
            continue
        hits.add(m.group(2))

    if typed_bare:
        for m in _SELF_PATTERN_A_BARE.finditer(bare):
            hits.add(_bare_hit(m.group(1)))
        for m in _SELF_PATTERN_B_BARE.finditer(bare):
            after = text[m.end():].lstrip()
            if after and after[0].isupper():
                continue
            hits.add(_bare_hit(m.group(2)))

    if len(hits) != 1:
        return None
    return hits.pop()
