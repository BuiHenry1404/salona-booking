import re

from app.core.errors import AppError


class InvalidPhoneError(AppError):
    message = "Số điện thoại không hợp lệ"


_VALID = re.compile(r"^0\d{9}$")


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
