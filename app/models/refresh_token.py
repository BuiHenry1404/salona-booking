from datetime import datetime
from typing import Optional

from app.models.base import BaseDocument


class RefreshToken(BaseDocument):
    """Một refresh token đang sống.

    Chỉ lưu **hash**, không bao giờ lưu giá trị thô: DB rò rỉ thì không được
    tương đương trao phiên đăng nhập của mọi khách.

    SHA-256 chứ không bcrypt — đây là bí mật ngẫu nhiên 256 bit do máy sinh,
    không phải mật khẩu người đặt, nên không có gì để dò và không cần hàm băm
    chậm. Đổi lại nó phải tra được bằng đúng giá trị ở mỗi lần refresh, việc mà
    bcrypt (mỗi lần băm ra một chuỗi khác) không làm được.
    """

    user_id: str
    # Mọi token xoay ra từ cùng một lần đăng nhập chung một `family_id`. Phát
    # hiện token bị đánh cắp thì xoá cả family — tức đá đúng một phiên, không
    # đụng các máy khác của cùng khách.
    family_id: str
    token_hash: str
    expires_at: datetime
    # Đánh dấu khi đã bị xoay. Token đã dùng mà xuất hiện lại = có người phát
    # lại — trừ trong cửa sổ ân hạn, xem `AuthService.consume_refresh_token`.
    used_at: Optional[datetime] = None
