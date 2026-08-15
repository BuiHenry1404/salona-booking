from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.models.base import BaseDocument

Role = Literal["user", "admin"]


class User(BaseDocument):
    """Khách hoặc chủ tiệm. Định danh bằng số điện thoại, không dùng email."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "phone": "0912345678",
                "full_name": "Nguyễn Thị Lan",
                "role": "user",
                "is_active": True,
            }
        },
    )

    phone: str = Field(..., min_length=10, max_length=10)
    hashed_password: str
    full_name: Optional[str] = None
    role: Role = "user"
    is_active: bool = True
    # Tăng mỗi lần đổi mật khẩu. Token mang `tv` khác giá trị này bị từ chối,
    # nếu không nạn nhân đổi lại mật khẩu mà token của kẻ chiếm vẫn sống tới
    # hết JWT_EXPIRE_MINUTES.
    token_version: int = 0


class CurrentUser(BaseDocument):
    """User đã xác thực, không mang trường nhạy cảm."""

    phone: str
    full_name: Optional[str] = None
    role: Role
    is_active: bool
