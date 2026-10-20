import unicodedata
from typing import Optional


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
