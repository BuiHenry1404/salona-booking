from typing import Literal, Optional

from pydantic import ConfigDict, Field, field_validator

from app.core.text import single_line
from app.models.base import BaseDocument

Role = Literal["user", "admin"]

# Độ dài đủ cho tên Việt dài nhất còn gặp trong thực tế, và đủ ngắn để một
# cái tên không nuốt mất khối bối cảnh.
FULL_NAME_MAX = 60


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

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: Optional[str]) -> Optional[str]:
        """Làm sạch tên trước khi nó đi vào khối bối cảnh.

        Khối bối cảnh mang vai HumanMessage và tự nói "hãy tin phần trên".
        Tên chứa xuống dòng là một lối để khách tự ghi thêm luật vào prompt.

        Đặt ở MODEL chứ không ở schema tạo user: tên bẩn đã nằm sẵn trong DB
        cũng được làm sạch lúc đọc lên, không cần migrate.

        `split()` không tham số gộp mọi loại khoảng trắng (space, \\n, \\r,
        \\t) thành một dấu cách — đúng thứ cần, và ngắn hơn một regex.
        """
        return single_line(value, FULL_NAME_MAX)

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
