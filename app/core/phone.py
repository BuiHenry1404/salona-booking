import re

from app.core.errors import AppError


class InvalidPhoneError(AppError):
    message = "Số điện thoại không hợp lệ"


# Chỉ nhận di động Việt Nam: đầu số 03, 05, 07, 08, 09 (quyết định của chủ dự
# án — tiệm không nhận số bàn). ^0\d{9}$ lỏng hơn nhưng cho lọt "0000000000" và
# "0123456789"; chủ tiệm gõ nhầm số của khách thì hệ thống vẫn tạo tài khoản,
# và sai sót chỉ lộ ra lúc khách không đăng nhập được — tức là đã quá muộn.
_VALID = re.compile(r"^0[35789]\d{8}$")


def normalize_phone(raw: str) -> str:
    """Quy mọi cách viết SĐT Việt Nam về một chuỗi duy nhất: 0XXXXXXXXX.

    Người lớn tuổi nhập mỗi lần một kiểu; không chuẩn hóa thì không đăng nhập được.
    """
    digits = re.sub(r"\D", "", raw or "")

    if digits.startswith("840"):
        digits = digits[2:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]

    if not _VALID.match(digits):
        raise InvalidPhoneError()
    return digits
