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
#
# Review Task 12 #2 (2026-10-26): "em hỏi chị làm được không" từng đọc nhầm
# thành khách tự xưng "chị" — thực ra "em" mới là người đang nói, "chị" ở đây
# là NGƯỜI ĐƯỢC HỎI (lễ tân). Cũng vậy "giúp chị hoa đặt lịch"/"cho chị lan
# làm móng": chữ ngay sau "chị"/"anh" trong mẫu nhờ vả là TÊN một người thứ
# ba, không phải tự xưng — kể cả gõ thường (khách lớn tuổi hiếm khi viết hoa
# tên riêng). Bộ lọc không dấu giờ chạy LUÔN (không chỉ khi cả câu không dấu)
# để bắt được câu gõ nửa dấu nửa không ("chị lam mong").
_SELF_VERBS = ("làm", "muốn", "đặt", "hủy", "dời", "đổi", "qua", "tới", "đến",
              "hỏi", "cần", "xem", "rảnh", "đi")
_SELF_VERBS_BARE = tuple(strip_diacritics(v) for v in _SELF_VERBS)

_SELF_PATTERN_A = re.compile(r"\b(chị|anh)\s+(" + "|".join(_SELF_VERBS) + r")\b",
                             re.IGNORECASE)
_SELF_PATTERN_B = re.compile(r"\b(cho|giúp|với)\s+(chị|anh)\b(?:\s+(\w+))?",
                             re.IGNORECASE)
_SELF_PATTERN_A_BARE = re.compile(r"\b(chi|anh)\s+(" + "|".join(_SELF_VERBS_BARE) + r")\b",
                                  re.IGNORECASE)
_SELF_PATTERN_B_BARE = re.compile(r"\b(cho|giup|voi)\s+(chi|anh)\b(?:\s+(\w+))?",
                                  re.IGNORECASE)

_ANH_CHI_TOGETHER = re.compile(r"\b(anh\s+chị|chị\s+anh)\b", re.IGNORECASE)
_ANH_CHI_TOGETHER_BARE = re.compile(r"\b(anh\s+chi|chi\s+anh)\b", re.IGNORECASE)

# Khách tự xưng bằng từ KHÁC "chị"/"anh" ("em"/"con"/"cháu"/"bé") — thì
# chị/anh xuất hiện ở đâu đó trong câu đang nói VỀ người khác (hỏi hộ, nhờ
# cho người khác), không phải khách tự xưng. Bỏ dấu riêng vì "còn" (bỏ dấu
# thành "con") là một chữ khác hẳn — chỉ so trên bản CÓ DẤU cho "con"/"cháu"/
# "bé", và so cả hai bản cho "em" (không dấu vốn đã giống).
_OTHER_SELF_WORDS = re.compile(r"\b(em|con|cháu|bé)\b", re.IGNORECASE)
_OTHER_SELF_WORDS_BARE = re.compile(r"\b(em|chau|be)\b", re.IGNORECASE)


def _bare_hit(token: str) -> str:
    return "chị" if token == "chi" else "anh"


def _is_name_like(word: Optional[str]) -> bool:
    """Chữ ngay sau "chị"/"anh" trong mẫu "cho/giúp/với" — không phải động từ
    tự xưng và không phải số/giờ ("3", "3h") thì coi là TÊN một người thứ ba
    ("giúp chị hoa đặt lịch", "cho chị lan làm móng"). Không chắc thì coi là
    tên (thận trọng) — bỏ qua khớp đó thay vì nhận liều.
    """
    if not word:
        return False
    if word[0].isdigit():
        return False
    return strip_diacritics(word.lower()) not in _SELF_VERBS_BARE


def detect_self_address(text: Optional[str]) -> Optional[Literal["anh", "chị"]]:
    """Khách đang tự xưng "chị" hay "anh", suy từ chính câu khách gõ — không
    đoán giới tính từ tên (tên khách dạng "Nguyễn Thị Lan" không nói lên
    điều đó). Trả None khi không chắc: "em"/"con"/"cháu"/"bé" đứng ở đâu đó
    trong câu (khách tự xưng từ khác, chị/anh đang nói về người khác), "anh
    chị" đi liền nhau (lời chào chung), "chị/anh + tên người thứ ba" ngay sau
    mẫu nhờ vả, hoặc cả hai đại từ cùng khớp trong một câu.
    """
    if not text:
        return None
    lowered = text.lower()
    bare = strip_diacritics(lowered)

    if _ANH_CHI_TOGETHER.search(lowered) or _ANH_CHI_TOGETHER_BARE.search(bare):
        return None
    if _OTHER_SELF_WORDS.search(lowered) or _OTHER_SELF_WORDS_BARE.search(bare):
        return None

    hits = set()
    for m in _SELF_PATTERN_A.finditer(lowered):
        hits.add(m.group(1))
    for m in _SELF_PATTERN_B.finditer(lowered):
        if not _is_name_like(m.group(3)):
            hits.add(m.group(2))
    # Bản không dấu chạy LUÔN (không chỉ khi cả câu không dấu): bắt được câu
    # gõ nửa dấu nửa không ("chị lam mong") mà bản có dấu ở trên bỏ sót.
    for m in _SELF_PATTERN_A_BARE.finditer(bare):
        hits.add(_bare_hit(m.group(1)))
    for m in _SELF_PATTERN_B_BARE.finditer(bare):
        if not _is_name_like(m.group(3)):
            hits.add(_bare_hit(m.group(2)))

    if len(hits) != 1:
        return None
    return hits.pop()
